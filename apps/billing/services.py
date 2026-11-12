"""
Wallet ledger + bill-payment orchestration.

Order lifecycle:
    card:   awaiting_payment --paid--> queued --sent--> processing --> successful
    wallet: (debited at creation)      queued --sent--> processing --> successful
    any failure after money was taken  --> refunded (full total back to wallet)

The biller's answer drives the order through `apply_biller_status`, whether it
arrives in the purchase response, a webhook, or a requery. Each transition
happens under a row lock and refunds are keyed by a unique ledger reference,
so concurrent webhook/requery calls can't double-refund.
"""
import logging
import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.phone import InvalidPhone, to_local
from apps.payments.gateways import get_gateway
from apps.payments.models import PaymentPurpose
from apps.payments.services import start_checkout

from .models import (
    BillOrder, BillService, BillStatus, PaymentMethod, TxnKind, TxnSource, Wallet,
    WalletTransaction,
)
from .vtu import VTUClient, VTUFailed, VTUUnknown

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ catalogue
# Slugs are VTU.ng Partner API v1 values.
NETWORKS = {"mtn": "MTN", "airtel": "Airtel", "glo": "Glo", "t2mobile": "T2 (9mobile)"}
DATA_NETWORKS = {**NETWORKS, "smile": "Smile"}
DISCOS = {
    "ikedc": "Ikeja Electric (IKEDC)", "ekedc": "Eko Electric (EKEDC)",
    "aedc": "Abuja Electric (AEDC)", "ibedc": "Ibadan Electric (IBEDC)",
    "eedc": "Enugu Electric (EEDC)", "phed": "Port Harcourt Electric (PHED)",
    "kedco": "Kano Electric (KEDCO)", "kaedco": "Kaduna Electric (KAEDCO)",
    "jed": "Jos Electric (JED)", "bedc": "Benin Electric (BEDC)",
    "abedc": "Aba Power (ABEDC)", "yedc": "Yola Electric (YEDC)",
}
TV_PROVIDERS = {"dstv": "DStv", "gotv": "GOtv", "startimes": "StarTimes"}
METER_TYPES = ("prepaid", "postpaid")
# ePINs come in sealed packs of 10 cards, 1–5 packs per purchase.
EPIN_VALUES = (100, 200, 500, 1000)
EPIN_PACK_SIZE = 10
EPIN_MAX_PACKS = 5
BETTING_PROVIDERS = {
    "1xbet": "1xBet", "bangbet": "BangBet", "bet9ja": "Bet9ja", "betking": "BetKing",
    "betland": "BetLand", "betlion": "BetLion", "betway": "Betway", "cloudbet": "CloudBet",
    "livescorebet": "LiveScore Bet", "merrybet": "MerryBet", "naijabet": "NaijaBet",
    "nairabet": "NairaBet", "sportybet": "SportyBet", "supabet": "SupaBet",
}
BETTING_MIN, BETTING_MAX = Decimal("100"), Decimal("100000")

AIRTIME_MIN = {"mtn": Decimal("10")}
AIRTIME_DEFAULT_MIN, AIRTIME_MAX = Decimal("50"), Decimal("50000")
ELECTRICITY_MIN, ELECTRICITY_MAX = Decimal("500"), Decimal("100000")

SUCCESS_STATUSES = {"completed"}
FAILED_STATUSES = {"failed", "refunded", "cancelled"}


def catalogue():
    return {
        "airtime": [{"id": k, "name": v} for k, v in NETWORKS.items()],
        "data": [{"id": k, "name": v} for k, v in DATA_NETWORKS.items()],
        "electricity": [{"id": k, "name": v} for k, v in DISCOS.items()],
        "tv": [{"id": k, "name": v} for k, v in TV_PROVIDERS.items()],
        "epins": [{"id": k, "name": v} for k, v in NETWORKS.items()],
        "betting": ([{"id": k, "name": v} for k, v in BETTING_PROVIDERS.items()]
                    if settings.BILL_BETTING_ENABLED else []),
        "betting_enabled": settings.BILL_BETTING_ENABLED,
        "meter_types": list(METER_TYPES),
        "epin_values": list(EPIN_VALUES),
        "epin_pack_size": EPIN_PACK_SIZE,
        "epin_max_quantity": EPIN_MAX_PACKS,
        "fees": {k: str(v) for k, v in settings.BILL_SERVICE_FEES.items()},
    }


def plans(kind, provider):
    """Data plans / TV bouquets, in the shape the web app uses."""
    client = VTUClient()
    items = client.data_plans(provider) if kind == "data" else client.cable_packages(provider)
    label = "data_plan" if kind == "data" else "package_bouquet"
    return [{"variation_id": i.get("variation_code"), label: i.get("name", ""),
             "price": str(i.get("amount")), "availability": "Available"}
            for i in items if i.get("variation_code") and i.get("amount") is not None]


# ------------------------------------------------------------------ wallet ledger
def wallet_for(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


class InsufficientFunds(ValidationError):
    default_code = "insufficient_funds"


def _post(user, *, kind, source, amount, reference, description, payment=None, order=None):
    """Write one ledger row and move the balance, atomically. Idempotent per reference."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError({"amount": "Amount must be positive."})
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=wallet_for(user).pk)
        existing = WalletTransaction.objects.filter(reference=reference).first()
        if existing:
            return existing
        if kind == TxnKind.DEBIT:
            if wallet.balance < amount:
                raise InsufficientFunds({"detail": "Insufficient wallet balance.",
                                         "code": "insufficient_funds"})
            wallet.balance -= amount
        else:
            wallet.balance += amount
        wallet.save(update_fields=["balance", "updated_at"])
        return WalletTransaction.objects.create(
            wallet=wallet, kind=kind, source=source, amount=amount,
            balance_after=wallet.balance, reference=reference, description=description[:255],
            payment=payment, bill_order=order,
        )


def credit_wallet_from_payment(payment):
    """Wallet top-up confirmed by the gateway."""
    _post(payment.payer, kind=TxnKind.CREDIT, source=TxnSource.TOPUP, amount=payment.amount,
          reference=f"topup:{payment.reference}", description="Wallet top-up", payment=payment)


def billing_gateway():
    """
    Bills and wallet top-ups are always collected through BILLING_GATEWAY
    (Flutterwave by default), never the client's choice.
    """
    name = settings.BILLING_GATEWAY
    if not get_gateway(name).is_configured():
        raise ValidationError({"detail": "Card payments for bills are temporarily unavailable. "
                                         "Please pay from your wallet or try again later.",
                               "code": "billing_gateway_unavailable"})
    return name


def start_wallet_topup(*, user, amount):
    amount = Decimal(amount)
    if not (settings.WALLET_MIN_TOPUP <= amount <= settings.WALLET_MAX_TOPUP):
        raise ValidationError({"amount": f"Top up between ₦{settings.WALLET_MIN_TOPUP:,.0f} and "
                                         f"₦{settings.WALLET_MAX_TOPUP:,.0f}."})
    wallet_for(user)
    return start_checkout(user=user, purpose=PaymentPurpose.WALLET_TOPUP, amount=amount,
                          description=f"Wallet top-up ₦{amount:,.2f}", gateway=billing_gateway())


# ------------------------------------------------------------------ quoting
def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"amount": "Enter a valid amount."}) from exc


def _find_variation(kind, provider, variation_id):
    try:
        items = plans(kind, provider)
    except (VTUUnknown, VTUFailed) as exc:
        raise ValidationError({"detail": "Plans are temporarily unavailable. Try again shortly."}) from exc
    for item in items:
        if str(item["variation_id"]) == str(variation_id):
            return item
    raise ValidationError({"variation_id": "Choose a valid plan."})


def build_order_fields(data):
    """
    Validate a purchase request and return the BillOrder fields.
    Prices for data and TV always come from the biller's list, never the client.
    """
    service = data.get("service")
    provider = (data.get("provider") or "").strip().lower()
    customer_id = re.sub(r"\s", "", str(data.get("customer_id") or ""))
    fields = {"service": service, "provider": provider}

    if service in (BillService.AIRTIME, BillService.DATA):
        networks = NETWORKS if service == BillService.AIRTIME else DATA_NETWORKS
        if provider not in networks:
            raise ValidationError({"provider": "Choose a network."})
        if provider != "smile":
            try:
                customer_id = to_local(customer_id)
            except InvalidPhone as exc:
                raise ValidationError({"customer_id": str(exc)}) from exc
        fields["customer_id"] = customer_id
        if service == BillService.AIRTIME:
            amount = _money(data.get("amount"))
            lo = AIRTIME_MIN.get(provider, AIRTIME_DEFAULT_MIN)
            if not (lo <= amount <= AIRTIME_MAX) or amount != amount.to_integral_value():
                raise ValidationError({"amount": f"Airtime must be a whole amount between "
                                                 f"₦{lo:,.0f} and ₦{AIRTIME_MAX:,.0f}."})
            fields.update(amount=amount, variation_label=f"{networks[provider]} airtime")
        else:
            plan = _find_variation("data", provider, data.get("variation_id"))
            fields.update(amount=_money(plan["price"]), variation_id=str(plan["variation_id"]),
                          variation_label=plan.get("data_plan", "")[:120])

    elif service == BillService.ELECTRICITY:
        if provider not in DISCOS:
            raise ValidationError({"provider": "Choose your electricity company."})
        meter_type = data.get("variation_id")
        if meter_type not in METER_TYPES:
            raise ValidationError({"variation_id": "Choose prepaid or postpaid."})
        if not re.fullmatch(r"\d{6,20}", customer_id):
            raise ValidationError({"customer_id": "Enter your meter / account number."})
        amount = _money(data.get("amount"))
        if not (ELECTRICITY_MIN <= amount <= ELECTRICITY_MAX) or amount != amount.to_integral_value():
            raise ValidationError({"amount": f"Enter a whole amount between ₦{ELECTRICITY_MIN:,.0f} "
                                             f"and ₦{ELECTRICITY_MAX:,.0f}."})
        fields.update(customer_id=customer_id, variation_id=meter_type, amount=amount,
                      variation_label=f"{DISCOS[provider]} · {meter_type}",
                      customer_name=(data.get("customer_name") or "")[:150])

    elif service == BillService.TV:
        if provider not in TV_PROVIDERS:
            raise ValidationError({"provider": "Choose your TV provider."})
        if not re.fullmatch(r"[\w-]{6,20}", customer_id):
            raise ValidationError({"customer_id": "Enter your smartcard / IUC number."})
        sub_type = data.get("subscription_type") or "change"
        if sub_type not in ("change", "renew"):
            raise ValidationError({"subscription_type": "Use change or renew."})
        renew = sub_type == "renew" and provider in ("dstv", "gotv")
        fields.update(customer_id=customer_id, subscription_type=sub_type if provider != "startimes" else "")
        if renew:
            # Renewal keeps the decoder's current bouquet at the price the provider quotes.
            try:
                info = VTUClient().verify_smartcard(provider=provider, smartcard_number=customer_id)
            except VTUFailed as exc:
                raise ValidationError({"customer_id": str(exc)}) from exc
            except VTUUnknown as exc:
                raise ValidationError({"detail": "Verification is temporarily unavailable."}) from exc
            price = info.get("renewal_amount") or info.get("current_bouquet_price")
            if not price:
                raise ValidationError({"subscription_type": "This decoder can't be renewed online. "
                                                            "Choose a bouquet instead."})
            fields.update(amount=_money(price), variation_id="",
                          variation_label=f"{TV_PROVIDERS[provider]} renewal · "
                                          f"{info.get('current_bouquet') or 'current bouquet'}"[:120],
                          customer_name=(info.get("customer_name") or "")[:150])
        else:
            plan = _find_variation("tv", provider, data.get("variation_id"))
            fields.update(amount=_money(plan["price"]), variation_id=str(plan["variation_id"]),
                          variation_label=f"{TV_PROVIDERS[provider]} {plan.get('package_bouquet', '')}"[:120],
                          customer_name=(data.get("customer_name") or "")[:150])

    elif service == BillService.EPINS:
        if provider not in NETWORKS:
            raise ValidationError({"provider": "Choose a network."})
        try:
            value = int(Decimal(str(data.get("variation_id") or data.get("value") or 0)))
            packs = int(data.get("quantity") or 1)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError({"variation_id": "Choose a PIN value."}) from exc
        if value not in EPIN_VALUES:
            raise ValidationError({"variation_id": "PINs come in ₦100, ₦200, ₦500 or ₦1,000."})
        if not (1 <= packs <= EPIN_MAX_PACKS):
            raise ValidationError({"quantity": f"Buy between 1 and {EPIN_MAX_PACKS} packs "
                                               f"of {EPIN_PACK_SIZE} cards."})
        fields.update(customer_id="", variation_id=str(value), quantity=packs,
                      amount=Decimal(value * EPIN_PACK_SIZE * packs),
                      variation_label=f"{NETWORKS[provider]} ₦{value} PINs · {packs} "
                                      f"pack{'s' if packs > 1 else ''} of {EPIN_PACK_SIZE}")

    elif service == BillService.BETTING:
        if not settings.BILL_BETTING_ENABLED:
            raise ValidationError({"service": "Betting wallet funding is not available."})
        if provider not in BETTING_PROVIDERS:
            raise ValidationError({"provider": "Choose your betting company."})
        if not re.fullmatch(r"[\w.@-]{3,40}", customer_id):
            raise ValidationError({"customer_id": "Enter your betting account ID."})
        amount = _money(data.get("amount"))
        if not (BETTING_MIN <= amount <= BETTING_MAX) or amount != amount.to_integral_value():
            raise ValidationError({"amount": f"Enter a whole amount between ₦{BETTING_MIN:,.0f} "
                                             f"and ₦{BETTING_MAX:,.0f}."})
        fields.update(customer_id=customer_id, amount=amount,
                      variation_label=f"{BETTING_PROVIDERS[provider]} wallet funding",
                      customer_name=(data.get("customer_name") or "")[:150])
    else:
        raise ValidationError({"service": "Choose airtime, data, electricity, tv, epins or betting."})

    fields["fee"] = settings.BILL_SERVICE_FEES.get(service, Decimal("0"))
    fields["total"] = fields["amount"] + fields["fee"]
    return fields


def verify_customer(*, service, provider, customer_id, meter_type=None):
    """Meter / smartcard / betting-account lookup, in the shape the web app uses."""
    provider = (provider or "").lower()
    customer_id = re.sub(r"\s", "", customer_id)
    client = VTUClient()
    try:
        if service == BillService.ELECTRICITY:
            if provider not in DISCOS:
                raise ValidationError({"provider": "Choose your electricity company."})
            v = client.verify_meter(disco=provider, meter_number=customer_id,
                                    meter_type=meter_type or "prepaid")
            return {"customer_name": v.get("customer_name"), "customer_address": v.get("address"),
                    "customer_arrears": v.get("customer_arrears"),
                    "min_purchase_amount": v.get("minimum_purchase_amount")}
        if service == BillService.TV:
            if provider not in TV_PROVIDERS:
                raise ValidationError({"provider": "Choose your TV provider."})
            v = client.verify_smartcard(provider=provider, smartcard_number=customer_id)
            return {"customer_name": v.get("customer_name"), "status": v.get("status"),
                    "due_date": v.get("due_date"), "current_bouquet": v.get("current_bouquet"),
                    "renewal_amount": v.get("renewal_amount") or v.get("current_bouquet_price"),
                    "supports_renewal": v.get("supports_renewal")}
        if service == BillService.BETTING:
            if not settings.BILL_BETTING_ENABLED or provider not in BETTING_PROVIDERS:
                raise ValidationError({"provider": "Choose your betting company."})
            v = client.verify_bet_account(provider=provider, account_id=customer_id)
            return {"customer_name": v.get("name") or v.get("account_name"),
                    "customer_username": v.get("username"),
                    "min_purchase_amount": v.get("min_payable_amount")}
    except VTUFailed as exc:
        raise ValidationError({"customer_id": str(exc)}) from exc
    except VTUUnknown as exc:
        raise ValidationError({"detail": "Verification is temporarily unavailable."}) from exc
    raise ValidationError({"service": "Nothing to verify for this service."})


# ------------------------------------------------------------------ ordering
def new_request_id():
    return f"JCB{timezone.now():%y%m%d}{uuid4().hex[:16]}"   # 25 chars (VTU max 50)


def create_order(*, user, data, payment_method):
    """Returns (order, authorization_url|None)."""
    fields = build_order_fields(data)
    if payment_method not in PaymentMethod.values:
        raise ValidationError({"payment_method": "Choose wallet or card."})

    if payment_method == PaymentMethod.WALLET:
        with transaction.atomic():
            order = BillOrder.objects.create(user=user, payment_method=payment_method,
                                             request_id=new_request_id(),
                                             status=BillStatus.QUEUED, **fields)
            _post(user, kind=TxnKind.DEBIT, source=TxnSource.BILL, amount=order.total,
                  reference=f"bill:{order.request_id}", order=order,
                  description=f"{order.get_service_display()} · {order.variation_label} · "
                              f"{order.customer_id}")
        dispatch_order(order)
        order.refresh_from_db()
        return order, None

    gateway = billing_gateway()
    holder = {}

    def link(payment):
        holder["order"] = BillOrder.objects.create(
            user=user, payment_method=payment_method, payment=payment,
            request_id=new_request_id(), status=BillStatus.AWAITING_PAYMENT, **fields)
        payment.metadata = {**payment.metadata, "bill_order_id": str(holder["order"].id)}
        payment.save(update_fields=["metadata", "updated_at"])

    label = f"{dict(BillService.choices)[fields['service']]} — {fields['variation_label']} " \
            f"({fields['customer_id']})"
    _payment, url = start_checkout(user=user, purpose=PaymentPurpose.BILL_PAYMENT,
                                   amount=fields["total"], description=label, gateway=gateway,
                                   before_gateway=link)
    return holder["order"], url


def fulfill_bill_payment(payment):
    """Card payment for a bill confirmed: queue it and send it to the biller."""
    order_id = (payment.metadata or {}).get("bill_order_id")
    order = BillOrder.objects.select_for_update().filter(pk=order_id, payment=payment).first()
    if not order:
        logger.error("Bill payment %s has no order", payment.reference)
        return
    if order.status == BillStatus.AWAITING_PAYMENT:
        order.status = BillStatus.QUEUED
        order.save(update_fields=["status", "updated_at"])
        transaction.on_commit(lambda: dispatch_order(order))


def purchase_payload(order):
    """The VTU.ng Partner API body for an order."""
    s = order.service
    if s == BillService.AIRTIME:
        return {"phone": order.customer_id, "network": order.provider, "amount": int(order.amount)}
    if s == BillService.DATA:
        return {"phone": order.customer_id, "network": order.provider,
                "variation_code": order.variation_id}
    if s == BillService.ELECTRICITY:
        return {"disco_slug": order.provider, "meter_number": order.customer_id,
                "meter_type": order.variation_id, "amount": int(order.amount)}
    if s == BillService.EPINS:
        return {"network": order.provider, "denomination": int(order.variation_id),
                "quantity": order.quantity}
    if s == BillService.BETTING:
        return {"provider": order.provider, "account_id": order.customer_id,
                "amount": int(order.amount)}
    body = {"provider": order.provider, "smartcard_number": order.customer_id, "quantity": 1}
    if order.subscription_type == "renew":
        body.update(subscription_type="renew", amount=int(order.amount))
    else:
        if order.subscription_type:
            body["subscription_type"] = "change"
        body["variation_code"] = order.variation_id
    return body


def _send(order):
    """POST the order (or re-POST it: the Idempotency-Key makes that a safe replay)."""
    try:
        result = VTUClient().purchase(order.service, purchase_payload(order),
                                      idempotency_key=order.request_id)
    except VTUFailed as exc:
        logger.warning("Bill %s refused by biller: %s %s", order.request_id, exc.code, exc)
        _fail_and_refund(order, f"{exc}", exc.body)
        return order
    except VTUUnknown as exc:
        logger.warning("Bill %s outcome unknown: %s — will requery", order.request_id, exc)
        BillOrder.objects.filter(pk=order.pk).update(
            provider_message=str(exc)[:255], updated_at=timezone.now())
        return order
    except Exception:  # noqa: BLE001 — must never lose the order
        logger.exception("Unexpected error sending bill %s", order.request_id)
        return order
    return apply_biller_status(order, result.transaction, source="purchase")


def dispatch_order(order):
    """Send a QUEUED order to VTU.ng. Never raises: outcomes are recorded on the order."""
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status != BillStatus.QUEUED:
            return order
        order.status = BillStatus.PROCESSING
        order.sent_at = timezone.now()
        order.save(update_fields=["status", "sent_at", "updated_at"])
    return _send(order)


def _store_epin_packs(order, cards):
    """Save each delivered pack's voucher image privately; keep only metadata on the order."""
    from django.core.files.base import ContentFile

    from apps.common.storage import private_storage

    storage, packs, client = private_storage(), [], VTUClient()
    for i, card in enumerate(cards):
        entry = {"pack_id": str(card.get("id") or i), "network": str(card.get("network") or ""),
                 "denomination": card.get("denomination"),
                 "pieces_count": card.get("pieces_count") or EPIN_PACK_SIZE, "file": ""}
        try:
            name = f"bill-epins/{order.user_id}/{order.id}-{i + 1}.webp"
            entry["file"] = storage.save(name, ContentFile(client.epin_image(card)))
        except Exception:  # noqa: BLE001 — keep the order; the image can be re-fetched by requery
            logger.exception("Could not store ePIN pack %s for %s", i, order.request_id)
        packs.append(entry)
    return packs


def apply_biller_status(order, tx, *, source):
    """Move an order according to VTU.ng's transaction (purchase/webhook/requery)."""
    from .vtu import transaction_status

    provider_status = transaction_status(tx)
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in (BillStatus.REFUNDED, BillStatus.CANCELLED):
            return order
        # A successful order only moves again if the biller later reverses it
        # (they refund us, so we refund the user).
        if order.status == BillStatus.SUCCESSFUL and provider_status not in FAILED_STATUSES:
            return order
        meta = tx.get("metadata") if isinstance(tx.get("metadata"), dict) else {}
        order.provider_status = provider_status[:40]
        order.provider_order_id = str(tx.get("reference") or order.provider_order_id or "")[:40]
        order.customer_name = str(meta.get("customer_name") or order.customer_name or "")[:150]
        order.token = str(meta.get("electricity_token") or meta.get("token") or order.token or "")[:120]
        order.units = str(meta.get("electricity_units") or order.units or "")[:40]
        if isinstance(tx.get("epin_cards"), list) and tx["epin_cards"] and not any(
                p.get("file") for p in (order.epins or []) if isinstance(p, dict)):
            order.epins = _store_epin_packs(order, tx["epin_cards"])
        # Never keep voucher images or other delivery secrets inside the raw payload.
        safe = {k: v for k, v in tx.items() if k not in ("epin_cards", "gift_card_codes",
                                                         "education_exam_pins")}
        order.response = {**(order.response or {}), source: safe}
        fields = ["provider_status", "provider_order_id", "customer_name", "token", "units",
                  "epins", "response", "updated_at"]
        if provider_status in SUCCESS_STATUSES:
            order.status = BillStatus.SUCCESSFUL
            order.completed_at = timezone.now()
            order.provider_message = ""
            fields += ["status", "completed_at", "provider_message"]
            order.save(update_fields=fields)
            transaction.on_commit(lambda: _notify_order(order))
            return order
        order.save(update_fields=fields)
    if provider_status in FAILED_STATUSES:
        _fail_and_refund(order, tx.get("failure_reason") or f"Biller status: {provider_status}", safe)
        order.refresh_from_db()
    return order


def _fail_and_refund(order, reason, body=None):
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in (BillStatus.REFUNDED, BillStatus.CANCELLED):
            return order
        order.provider_message = str(reason)[:255]
        if body:
            order.response = {**(order.response or {}), "failure": body}
        money_taken = order.payment_method == PaymentMethod.WALLET or order.payment_id
        if money_taken and order.status != BillStatus.AWAITING_PAYMENT:
            _post(order.user, kind=TxnKind.CREDIT, source=TxnSource.REFUND, amount=order.total,
                  reference=f"refund:{order.request_id}", order=order,
                  description=f"Refund · {order.get_service_display()} {order.customer_id}")
            order.status = BillStatus.REFUNDED
            order.refunded_at = timezone.now()
        else:
            order.status = BillStatus.FAILED
        order.save(update_fields=["status", "refunded_at", "provider_message", "response",
                                  "updated_at"])
        transaction.on_commit(lambda: _notify_order(order))
    return order


def requery_order(order):
    """
    Refresh an order we have no final answer for. Looks it up by VTU.ng's
    reference, or by our Idempotency-Key if the purchase response never came
    back. If VTU.ng has no record, the order is re-sent with the same key
    (it can't be bought twice); after 30 minutes without a record we refund.
    """
    if order.status not in (BillStatus.PROCESSING, BillStatus.QUEUED):
        return order
    if order.status == BillStatus.QUEUED:
        return dispatch_order(order)
    from .vtu import is_sandbox

    try:
        result = VTUClient().get_transaction(reference=order.provider_order_id or None,
                                             idempotency_key=order.request_id)
    except VTUFailed as exc:
        if exc.code != "not_found" or is_sandbox():
            # The sandbox never stores transactions: the webhook settles them.
            return order
        if order.provider_order_id:
            return order          # VTU.ng gave us a reference: it exists, just not visible yet
        age = timezone.now() - (order.sent_at or order.created_at)
        if age > timedelta(minutes=30):
            return _fail_and_refund(order, "Order never reached the biller.")
        return _send(order)
    except VTUUnknown:
        return order
    return apply_biller_status(order, result.transaction, source="requery")


def requery_stale_orders(min_age_minutes=2, max_age_hours=72):
    now = timezone.now()
    qs = BillOrder.objects.filter(
        status__in=[BillStatus.PROCESSING, BillStatus.QUEUED],
        updated_at__lte=now - timedelta(minutes=min_age_minutes),
        created_at__gte=now - timedelta(hours=max_age_hours),
    )
    n = 0
    for order in qs:
        requery_order(order)
        n += 1
    return n


def _notify_order(order):
    from apps.notifications.services import notify

    if order.status == BillStatus.SUCCESSFUL:
        extra = (f" Token: {order.token}." if order.token
                 else " Your PINs are on the receipt in your JobCatch account." if order.epins else "")
        notify(order.user, kind="bill_alert", title=f"{order.get_service_display()} successful",
               body=f"₦{order.amount:,.0f} {order.variation_label}"
                    f"{f' for {order.customer_id}' if order.customer_id else ''} was delivered.{extra}",
               data={"bill_order_id": str(order.id), "url": f"/bills/receipt/{order.id}"})
    elif order.status == BillStatus.REFUNDED:
        notify(order.user, kind="bill_alert", title=f"{order.get_service_display()} failed — refunded",
               body=f"We couldn't complete your {order.variation_label} purchase"
                    f"{f' for {order.customer_id}' if order.customer_id else ''}. "
                    f"₦{order.total:,.2f} was returned to your wallet.",
               data={"bill_order_id": str(order.id), "url": f"/bills/receipt/{order.id}"})
