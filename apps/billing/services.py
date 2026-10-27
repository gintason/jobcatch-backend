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
from apps.payments.models import PaymentPurpose
from apps.payments.services import start_checkout

from .models import (
    BillOrder, BillService, BillStatus, PaymentMethod, TxnKind, TxnSource, Wallet,
    WalletTransaction,
)
from .vtu import VTUClient, VTUFailed, VTUUnknown

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ catalogue
NETWORKS = {"mtn": "MTN", "airtel": "Airtel", "glo": "Glo", "9mobile": "9mobile"}
DATA_NETWORKS = {**NETWORKS, "smile": "Smile"}
DISCOS = {
    "ikeja-electric": "Ikeja Electric (IKEDC)", "eko-electric": "Eko Electric (EKEDC)",
    "abuja-electric": "Abuja Electric (AEDC)", "ibadan-electric": "Ibadan Electric (IBEDC)",
    "enugu-electric": "Enugu Electric (EEDC)", "portharcourt-electric": "Port Harcourt Electric (PHED)",
    "kano-electric": "Kano Electric (KEDCO)", "kaduna-electric": "Kaduna Electric (KAEDCO)",
    "jos-electric": "Jos Electric (JED)", "benin-electric": "Benin Electric (BEDC)",
    "aba-electric": "Aba Power (ABEDC)", "yola-electric": "Yola Electric (YEDC)",
}
TV_PROVIDERS = {"dstv": "DStv", "gotv": "GOtv", "startimes": "StarTimes", "showmax": "Showmax"}
METER_TYPES = ("prepaid", "postpaid")

AIRTIME_MIN = {"mtn": Decimal("10")}
AIRTIME_DEFAULT_MIN, AIRTIME_MAX = Decimal("50"), Decimal("50000")
ELECTRICITY_MIN, ELECTRICITY_MAX = Decimal("500"), Decimal("100000")

SUCCESS_STATUSES = {"completed-api"}
FAILED_STATUSES = {"refunded", "failed", "cancelled"}


def catalogue():
    return {
        "airtime": [{"id": k, "name": v} for k, v in NETWORKS.items()],
        "data": [{"id": k, "name": v} for k, v in DATA_NETWORKS.items()],
        "electricity": [{"id": k, "name": v} for k, v in DISCOS.items()],
        "tv": [{"id": k, "name": v} for k, v in TV_PROVIDERS.items()],
        "meter_types": list(METER_TYPES),
        "fees": {k: str(v) for k, v in settings.BILL_SERVICE_FEES.items()},
    }


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


def start_wallet_topup(*, user, amount, gateway=None):
    amount = Decimal(amount)
    if not (settings.WALLET_MIN_TOPUP <= amount <= settings.WALLET_MAX_TOPUP):
        raise ValidationError({"amount": f"Top up between ₦{settings.WALLET_MIN_TOPUP:,.0f} and "
                                         f"₦{settings.WALLET_MAX_TOPUP:,.0f}."})
    wallet_for(user)
    return start_checkout(user=user, purpose=PaymentPurpose.WALLET_TOPUP, amount=amount,
                          description=f"Wallet top-up ₦{amount:,.2f}", gateway=gateway)


# ------------------------------------------------------------------ quoting
def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"amount": "Enter a valid amount."}) from exc


def _find_variation(kind, provider, variation_id):
    try:
        items = VTUClient().variations(kind, provider)
    except VTUUnknown as exc:
        raise ValidationError({"detail": "Plans are temporarily unavailable. Try again shortly."}) from exc
    for item in items:
        if str(item.get("variation_id")) == str(variation_id):
            if str(item.get("availability", "Available")).lower() != "available":
                raise ValidationError({"variation_id": "That plan is currently unavailable."})
            return item
    raise ValidationError({"variation_id": "Choose a valid plan."})


def build_order_fields(data):
    """
    Validate a purchase request and return the BillOrder fields.
    Prices for data and TV always come from the biller's list, never the client.
    """
    service = data.get("service")
    provider = (data.get("provider") or "").strip()
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
        plan = _find_variation("tv", provider, data.get("variation_id"))
        amount = _money(plan["price"])
        if sub_type == "renew" and provider in ("dstv", "gotv"):
            # Renewal price comes from the decoder itself (may be discounted).
            try:
                info = VTUClient().verify_customer(customer_id=customer_id, service_id=provider)
            except VTUFailed as exc:
                raise ValidationError({"customer_id": str(exc)}) from exc
            if info.get("renewal_amount"):
                amount = _money(info["renewal_amount"])
            fields["customer_name"] = (info.get("customer_name") or "")[:150]
        fields.update(customer_id=customer_id, variation_id=str(plan["variation_id"]),
                      variation_label=f"{TV_PROVIDERS[provider]} {plan.get('package_bouquet', '')}"[:120],
                      subscription_type=sub_type if provider in ("dstv", "gotv") else "",
                      amount=amount)
    else:
        raise ValidationError({"service": "Choose airtime, data, electricity or tv."})

    fields["fee"] = settings.BILL_SERVICE_FEES.get(service, Decimal("0"))
    fields["total"] = fields["amount"] + fields["fee"]
    return fields


def verify_customer(*, service, provider, customer_id, meter_type=None):
    if service == BillService.ELECTRICITY and provider not in DISCOS:
        raise ValidationError({"provider": "Choose your electricity company."})
    if service == BillService.TV and provider not in TV_PROVIDERS:
        raise ValidationError({"provider": "Choose your TV provider."})
    try:
        return VTUClient().verify_customer(
            customer_id=re.sub(r"\s", "", customer_id), service_id=provider,
            variation_id=meter_type if service == BillService.ELECTRICITY else None)
    except VTUFailed as exc:
        raise ValidationError({"customer_id": str(exc)}) from exc
    except VTUUnknown as exc:
        raise ValidationError({"detail": "Verification is temporarily unavailable."}) from exc


# ------------------------------------------------------------------ ordering
def new_request_id():
    return f"JCB{timezone.now():%y%m%d}{uuid4().hex[:16]}"   # 25 chars (VTU max 50)


def create_order(*, user, data, payment_method, gateway=None):
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


def dispatch_order(order):
    """Send a QUEUED order to VTU.ng. Never raises: outcomes are recorded on the order."""
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status != BillStatus.QUEUED:
            return order
        order.status = BillStatus.PROCESSING
        order.sent_at = timezone.now()
        order.save(update_fields=["status", "sent_at", "updated_at"])

    client = VTUClient()
    try:
        if order.service == BillService.AIRTIME:
            result = client.buy_airtime(request_id=order.request_id, phone=order.customer_id,
                                        service_id=order.provider, amount=order.amount)
        elif order.service == BillService.DATA:
            result = client.buy_data(request_id=order.request_id, phone=order.customer_id,
                                     service_id=order.provider, variation_id=order.variation_id)
        elif order.service == BillService.ELECTRICITY:
            result = client.buy_electricity(request_id=order.request_id,
                                            customer_id=order.customer_id,
                                            service_id=order.provider,
                                            variation_id=order.variation_id, amount=order.amount)
        else:
            renew = order.subscription_type == "renew"
            result = client.buy_tv(request_id=order.request_id, customer_id=order.customer_id,
                                   service_id=order.provider, variation_id=order.variation_id,
                                   subscription_type=order.subscription_type or "change",
                                   amount=order.amount if renew else None)
    except VTUFailed as exc:
        logger.warning("Bill %s refused by biller: %s", order.request_id, exc)
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
    return apply_biller_status(order, result.data, source="purchase")


def apply_biller_status(order, data, *, source):
    """Move an order according to the biller's order data (purchase/webhook/requery)."""
    provider_status = str(data.get("status") or "")
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in (BillStatus.REFUNDED, BillStatus.CANCELLED):
            return order
        # A successful order only moves again if the biller later reverses it
        # (they refund us, so we refund the user).
        if order.status == BillStatus.SUCCESSFUL and provider_status not in FAILED_STATUSES:
            return order
        order.provider_status = provider_status
        order.provider_order_id = str(data.get("order_id") or order.provider_order_id or "")
        order.customer_name = (data.get("customer_name") or order.customer_name or "")[:150]
        order.token = str(data.get("token") or order.token or "")[:120]
        order.units = str(data.get("units") or order.units or "")[:40]
        order.response = {**(order.response or {}), source: data}
        fields = ["provider_status", "provider_order_id", "customer_name", "token", "units",
                  "response", "updated_at"]
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
        _fail_and_refund(order, f"Biller status: {provider_status}", data)
        order.refresh_from_db()
    return order


def _fail_and_refund(order, reason, body=None):
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status in (BillStatus.REFUNDED, BillStatus.CANCELLED):
            return order
        order.provider_message = reason[:255]
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
    if order.status not in (BillStatus.PROCESSING, BillStatus.QUEUED):
        return order
    if order.status == BillStatus.QUEUED:
        return dispatch_order(order)
    try:
        result = VTUClient().requery(order.request_id)
    except VTUFailed as exc:
        if exc.code == "order_not_found" and order.sent_at and \
                timezone.now() - order.sent_at > timedelta(minutes=10):
            return _fail_and_refund(order, "Order never reached the biller.")
        return order
    except VTUUnknown:
        return order
    return apply_biller_status(order, result.data, source="requery")


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
        extra = f" Token: {order.token}." if order.token else ""
        notify(order.user, kind="bill_alert", title=f"{order.get_service_display()} successful",
               body=f"₦{order.amount:,.0f} {order.variation_label} for {order.customer_id} "
                    f"was delivered.{extra}",
               data={"bill_order_id": str(order.id), "url": f"/bills/receipt/{order.id}"})
    elif order.status == BillStatus.REFUNDED:
        notify(order.user, kind="bill_alert", title=f"{order.get_service_display()} failed — refunded",
               body=f"We couldn't complete your {order.variation_label} purchase for "
                    f"{order.customer_id}. ₦{order.total:,.2f} was returned to your wallet.",
               data={"bill_order_id": str(order.id), "url": f"/bills/receipt/{order.id}"})
