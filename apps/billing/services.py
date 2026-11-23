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

EDUCATION_PROVIDERS = {"waec": "WAEC", "jamb": "JAMB"}
EDUCATION_SERVICE_TYPES = {"result_checker": "Result checker", "registration": "Registration",
                           "jamb": "JAMB"}
EDUCATION_MAX_QUANTITY = 10
GIFTCARD_MAX_QUANTITY = 10
RMB_CHANNELS = {"alipay": "Alipay", "wechat": "WeChat Pay", "bank": "Chinese bank transfer"}
RMB_ACCOUNT_TYPES = ("nigerian", "chinese")

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
        "education": ([{"id": k, "name": v} for k, v in EDUCATION_PROVIDERS.items()]
                      if settings.BILL_EDUCATION_ENABLED else []),
        "education_enabled": settings.BILL_EDUCATION_ENABLED,
        "education_max_quantity": EDUCATION_MAX_QUANTITY,
        "giftcard_buy_enabled": settings.BILL_GIFTCARD_BUY_ENABLED,
        "giftcard_sell_enabled": settings.BILL_GIFTCARD_SELL_ENABLED,
        "giftcard_max_quantity": GIFTCARD_MAX_QUANTITY,
        "rmb_enabled": settings.BILL_RMB_ENABLED,
        "meter_types": list(METER_TYPES),
        "epin_values": list(EPIN_VALUES),
        "epin_pack_size": EPIN_PACK_SIZE,
        "epin_max_quantity": EPIN_MAX_PACKS,
        "fees": {k: str(v) for k, v in settings.BILL_SERVICE_FEES.items()},
    }


def epin_stock():
    """Packs in stock per network and value; {} when the biller can't say (don't block sales)."""
    from .vtu import VTUClient, VTUFailed, VTUNotConfigured, VTUUnknown

    try:
        return VTUClient().epin_stock()
    except (VTUFailed, VTUUnknown, VTUNotConfigured):
        logger.warning("ePIN stock unavailable; not blocking purchases on it")
        return {}
    except Exception:  # noqa: BLE001 — an unexpected shape must not stop PIN sales
        logger.exception("Could not read ePIN stock")
        return {}


def plans(kind, provider, service_type=None):
    """Data plans / TV bouquets / exam PINs, in the shape the web app uses."""
    client = VTUClient()
    if kind == "education":
        items = client.education_plans(provider, service_type)
    else:
        items = client.data_plans(provider) if kind == "data" else client.cable_packages(provider)
    label = "package_bouquet" if kind == "tv" else "data_plan"
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


def _find_variation(kind, provider, variation_id, service_type=None):
    try:
        items = plans(kind, provider, service_type)
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
        in_stock = epin_stock().get(provider, {}).get(value)
        if in_stock is not None and in_stock < packs:
            raise ValidationError({"variation_id": (
                f"{NETWORKS[provider]} ₦{value} PINs are out of stock right now. Try another value."
                if in_stock == 0 else f"Only {in_stock} pack{'s' if in_stock > 1 else ''} of "
                f"{NETWORKS[provider]} ₦{value} PINs left — reduce the quantity.")})
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
    elif service == BillService.EDUCATION:
        _education_fields(fields, data, provider, customer_id)
    elif service == BillService.GIFTCARD:
        _giftcard_fields(fields, data)
    elif service == BillService.RMB:
        _rmb_fields(fields, data)
    else:
        raise ValidationError({"service": "Choose a bill type."})

    fields["fee"] = settings.BILL_SERVICE_FEES.get(service, Decimal("0"))
    fields["total"] = fields["amount"] + fields["fee"]
    return fields


def _pct(amount, percent):
    return (Decimal(amount) * Decimal(percent) / 100).quantize(Decimal("0.01"))


def _education_fields(fields, data, provider, customer_id):
    if not settings.BILL_EDUCATION_ENABLED:
        raise ValidationError({"service": "Exam PINs are not available right now."})
    if provider not in EDUCATION_PROVIDERS:
        raise ValidationError({"provider": "Choose WAEC or JAMB."})
    service_type = data.get("service_type") or ("jamb" if provider == "jamb" else "result_checker")
    if service_type not in EDUCATION_SERVICE_TYPES:
        raise ValidationError({"service_type": "Choose a service."})
    try:
        quantity = int(data.get("quantity") or 1)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"quantity": "Enter a quantity."}) from exc
    if not (1 <= quantity <= EDUCATION_MAX_QUANTITY):
        raise ValidationError({"quantity": f"Buy between 1 and {EDUCATION_MAX_QUANTITY}."})
    plan = _find_variation("education", provider, data.get("variation_id"), service_type)
    if provider == "jamb":
        if not re.fullmatch(r"[\w-]{4,30}", customer_id):
            raise ValidationError({"customer_id": "Enter the candidate's JAMB profile ID."})
        quantity = 1
    else:
        customer_id = ""
    fields.update(customer_id=customer_id, service_type=service_type, quantity=quantity,
                  variation_id=str(plan["variation_id"]),
                  amount=_money(Decimal(plan["price"]) * quantity),
                  variation_label=(f"{plan.get('data_plan') or EDUCATION_PROVIDERS[provider]}"
                                   f"{f' × {quantity}' if quantity > 1 else ''}")[:120],
                  customer_name=(data.get("customer_name") or "")[:150])


def giftcard_quote(*, product_id, amount, quantity):
    """VTU.ng's NGN price for a gift card + JobCatch's margin."""
    if not settings.BILL_GIFTCARD_BUY_ENABLED:
        raise ValidationError({"service": "Gift cards are not available right now."})
    try:
        product_id, quantity = int(product_id), int(quantity or 1)
        amount = Decimal(str(amount))
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"amount": "Choose a card and amount."}) from exc
    if not (1 <= quantity <= GIFTCARD_MAX_QUANTITY) or amount <= 0:
        raise ValidationError({"quantity": f"Buy between 1 and {GIFTCARD_MAX_QUANTITY} cards."})
    try:
        price = VTUClient().giftcard_price(product_id=product_id, amount=float(amount),
                                           quantity=quantity)
    except VTUFailed as exc:
        raise ValidationError({"amount": str(exc)}) from exc
    except VTUUnknown as exc:
        raise ValidationError({"detail": "Gift card prices are temporarily unavailable."}) from exc
    cost = _money(price.get("total_price_ngn"))
    return {"cost_ngn": cost, "price_ngn": cost + _pct(cost, settings.GIFTCARD_BUY_MARGIN_PERCENT),
            "quantity": quantity, "amount": amount, "product_id": product_id}


def _giftcard_fields(fields, data):
    quote = giftcard_quote(product_id=data.get("product_id"), amount=data.get("card_amount"),
                           quantity=data.get("quantity"))
    name = (data.get("product_name") or "Gift card")[:80]
    currency = (data.get("card_currency") or "")[:3].upper()
    fields.update(customer_id="", quantity=quote["quantity"], amount=quote["price_ngn"],
                  variation_id=str(quote["product_id"]),
                  variation_label=(f"{name} · {currency} {quote['amount']:,.2f}"
                                   + (f" × {quote['quantity']}" if quote["quantity"] > 1 else ""))[:120],
                  details={"product_id": quote["product_id"], "card_amount": str(quote["amount"]),
                           "card_currency": currency, "product_name": name,
                           "expected_amount_ngn": str(quote["cost_ngn"])})


def rmb_rate(channel, account_type, rmb_amount):
    """NGN per ¥1 for this order from VTU.ng's tiers, plus the channel's limits."""
    data = VTUClient().rmb_rates()
    limits = data.get("limits") or {}
    for ch in data.get("channels") or []:
        if ch.get("channel") != channel:
            continue
        if not ch.get("is_available", True):
            raise ValidationError({"channel": f"{RMB_CHANNELS[channel]} is unavailable right now."})
        tiers = ch.get("rates") or []
        if channel != "bank":
            acct = next((a for a in ch.get("account_types") or [] if a.get("type") == account_type), None)
            if not acct or not acct.get("is_available", True):
                raise ValidationError({"account_type": "That account type is unavailable right now."})
            tiers = acct.get("rates") or tiers
        for t in tiers:
            lo, hi = Decimal(str(t.get("min_rmb") or 0)), t.get("max_rmb")
            if rmb_amount >= lo and (hi is None or rmb_amount <= Decimal(str(hi))):
                return Decimal(str(t["ngn_per_rmb"])), limits
        raise ValidationError({"rmb_amount": "No rate for that amount."})
    raise ValidationError({"channel": "Choose a payment channel."})


def rmb_quote(*, channel, account_type, rmb_amount):
    if not settings.BILL_RMB_ENABLED:
        raise ValidationError({"service": "RMB payments are not available right now."})
    if channel not in RMB_CHANNELS:
        raise ValidationError({"channel": "Choose Alipay, WeChat Pay or bank transfer."})
    if channel != "bank" and account_type not in RMB_ACCOUNT_TYPES:
        raise ValidationError({"account_type": "Choose a Nigerian or Chinese account."})
    try:
        rmb_amount = Decimal(str(rmb_amount)).quantize(Decimal("0.01"))
        rate, limits = rmb_rate(channel, account_type, rmb_amount)
    except (VTUUnknown, VTUFailed) as exc:
        raise ValidationError({"detail": "RMB rates are temporarily unavailable."}) from exc
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise ValidationError({"rmb_amount": "Enter an amount in yuan."}) from exc
    lo, hi = Decimal(str(limits.get("min_rmb") or 1)), Decimal(str(limits.get("max_rmb") or 10**9))
    if not (lo <= rmb_amount <= hi):
        raise ValidationError({"rmb_amount": f"Send between ¥{lo:,.0f} and ¥{hi:,.0f}."})
    cost = _money(rmb_amount * rate)
    margin = _pct(cost, settings.RMB_MARGIN_PERCENT)
    return {"rmb_amount": rmb_amount, "rate": rate, "cost_ngn": cost, "price_ngn": cost + margin,
            "customer_rate": ((cost + margin) / rmb_amount).quantize(Decimal("0.01"))}


def _rmb_fields(fields, data):
    channel, account_type = data.get("channel"), data.get("account_type") or ""
    q = rmb_quote(channel=channel, account_type=account_type, rmb_amount=data.get("rmb_amount"))
    name = (data.get("recipient_name") or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,99}", name):
        raise ValidationError({"recipient_name": "Enter the recipient's full name (letters only)."})
    description = (data.get("description") or "").strip()
    if not (5 <= len(description) <= 1000):
        raise ValidationError({"description": "Say what the payment is for (5–1,000 characters)."})
    identifier = re.sub(r"\s", "", data.get("recipient_identifier") or "")
    details = {"channel": channel, "rmb_amount": str(q["rmb_amount"]), "ngn_per_rmb": str(q["rate"]),
               "customer_rate": str(q["customer_rate"]), "cost_ngn": str(q["cost_ngn"]),
               "recipient_name": name, "recipient_identifier": identifier[:100],
               "description": description}
    if channel == "bank":
        bank, account = (data.get("recipient_bank_name") or "").strip(), re.sub(r"\D", "", data.get(
            "recipient_bank_account_number") or "")
        if not bank or not (8 <= len(account) <= 30):
            raise ValidationError({"recipient_bank_account_number": "Enter the Chinese bank and account number."})
        details.update(recipient_bank_name=bank[:100], recipient_bank_account_number=account,
                       recipient_identifier=account)
    else:
        details["account_type"] = account_type
        if not data.get("qr_code"):
            raise ValidationError({"qr_code": f"Upload the recipient's {RMB_CHANNELS[channel]} receive QR code."})
    fields.update(customer_id=(identifier or name)[:40], customer_name=name[:150],
                  amount=q["price_ngn"], details=details,
                  variation_label=f"¥{q['rmb_amount']:,.2f} via {RMB_CHANNELS[channel]}"[:120])


def verify_customer(*, service, provider, customer_id, meter_type=None, variation_code=None):
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
        if service == BillService.EDUCATION:
            if provider != "jamb":
                raise ValidationError({"provider": "Only JAMB profiles can be verified."})
            if not variation_code:
                raise ValidationError({"variation_id": "Choose the JAMB service first."})
            v = client.verify_jamb(profile_id=customer_id, variation_code=variation_code)
            return {"customer_name": v.get("customer_name"), "exam_type": v.get("exam_type")}
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
    if data.get("qr_code") and fields["service"] == BillService.RMB:
        fields["attachment"] = data["qr_code"]
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
    if s == BillService.EDUCATION:
        body = {"provider": order.provider, "service_type": order.service_type,
                "variation_code": order.variation_id, "quantity": order.quantity}
        if order.provider == "jamb":
            body["profile_id"] = order.customer_id
        return body
    d = order.details or {}
    if s == BillService.GIFTCARD:
        return {"product_id": int(d["product_id"]), "amount": float(d["card_amount"]),
                "quantity": order.quantity, "expected_amount_ngn": float(d["expected_amount_ngn"])}
    if s == BillService.RMB:
        body = {"channel": d["channel"], "rmb_amount": d["rmb_amount"],
                "recipient_name": d["recipient_name"], "description": d["description"]}
        if d["channel"] == "bank":
            body.update(recipient_identifier=d["recipient_bank_account_number"],
                        recipient_bank_name=d["recipient_bank_name"],
                        recipient_bank_account_number=d["recipient_bank_account_number"])
        else:
            body[f"{d['channel']}_account_type"] = d["account_type"]
            if d.get("recipient_identifier"):
                body["recipient_identifier"] = d["recipient_identifier"]
        return body
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
        files = None
        if order.service == BillService.RMB and order.attachment:
            import mimetypes

            with order.attachment.open("rb") as fh:
                name = order.attachment.name.rsplit("/", 1)[-1]
                files = [("qr_code", name, fh.read(),
                          mimetypes.guess_type(name)[0] or "image/jpeg")]
        result = VTUClient().purchase(order.service, purchase_payload(order),
                                      idempotency_key=order.request_id, files=files)
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
        codes = _delivered_codes(tx)
        if codes:
            order.epins = codes
        if isinstance(tx.get("order"), dict):          # RMB: the rate VTU.ng actually applied
            o = tx["order"]
            ngn = o.get("ngn_amount") or {}
            order.details = {**(order.details or {}), "provider_rate": o.get("exchange_rate"),
                             "provider_ngn": ngn.get("raw") if isinstance(ngn, dict) else ngn}
        # Never keep voucher images or other delivery secrets inside the raw payload.
        safe = {k: v for k, v in tx.items() if k not in ("epin_cards", "gift_card_codes",
                                                         "education_exam_pins")}
        order.response = {**(order.response or {}), source: safe}
        fields = ["provider_status", "provider_order_id", "customer_name", "token", "units",
                  "epins", "details", "response", "updated_at"]
        if provider_status in SUCCESS_STATUSES and _awaiting_codes(order) and source != "lookup":
            # Exam PINs and gift card codes are only returned by the transaction
            # lookup (never in webhooks): fetch them before marking it delivered.
            order.save(update_fields=fields)
            fetch = order.provider_order_id
        elif provider_status in SUCCESS_STATUSES:
            order.status = BillStatus.SUCCESSFUL
            order.completed_at = timezone.now()
            order.provider_message = ""
            fields += ["status", "completed_at", "provider_message"]
            order.save(update_fields=fields)
            transaction.on_commit(lambda: _notify_order(order))
            return order
        else:
            order.save(update_fields=fields)
            fetch = None
    if provider_status in SUCCESS_STATUSES and fetch:
        try:
            looked_up = VTUClient().get_transaction(reference=fetch).transaction
        except (VTUFailed, VTUUnknown) as exc:
            logger.warning("Order %s completed but codes not fetched yet (%s); requery will retry",
                           order.request_id, exc)
            return order
        return apply_biller_status(order, looked_up, source="lookup")
    if provider_status in FAILED_STATUSES:
        _fail_and_refund(order, tx.get("failure_reason") or f"Biller status: {provider_status}", safe)
        order.refresh_from_db()
    return order


def _awaiting_codes(order):
    from .vtu import is_sandbox

    # The sandbox doesn't store transactions, so there's nothing to look up there.
    return (order.service in (BillService.EDUCATION, BillService.GIFTCARD) and not order.epins
            and not is_sandbox())


def _delivered_codes(tx):
    """Exam PINs / gift card codes in the receipt's {pin, serial, amount, instruction} shape."""
    out = []
    pins = tx.get("education_exam_pins")
    if isinstance(pins, dict):
        pins = pins.get("pins") or list(pins.values())
    for p in pins or []:
        if isinstance(p, dict):
            out.append({"pin": str(p.get("pin") or p.get("token") or ""),
                        "serial": str(p.get("serial") or p.get("serial_number") or ""),
                        "amount": "", "instruction": ""})
    for c in tx.get("gift_card_codes") or []:
        if isinstance(c, dict):
            out.append({"pin": str(c.get("cardNumber") or c.get("code") or ""),
                        "serial": str(c.get("pinCode") or ""), "amount": "",
                        "instruction": str(c.get("claimUrl") or "")})
    return [c for c in out if c["pin"]]


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


REFUNDABLE_BY_ADMIN = (BillStatus.SUCCESSFUL, BillStatus.PROCESSING, BillStatus.QUEUED)


def admin_refund(order, *, admin, reason):
    """
    Staff refund of a bill order to the customer's JobCatch wallet — e.g. the biller
    reported "completed" but the customer never received the airtime/data and the
    biller has confirmed non-delivery. Refunds the full amount paid (incl. fee),
    exactly once, records who did it and why, and tells the customer.
    """
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5:
        raise ValidationError({"reason": "Give a short reason for the refund (it's kept on the order)."})
    with transaction.atomic():
        order = BillOrder.objects.select_for_update().get(pk=order.pk)
        if order.status == BillStatus.REFUNDED:
            raise ValidationError({"detail": "This order has already been refunded."})
        if order.status not in REFUNDABLE_BY_ADMIN:
            raise ValidationError({"detail": f"A {order.get_status_display().lower()} order can't be refunded — "
                                             "no money was taken, or it was already returned."})
        if not (order.payment_method == PaymentMethod.WALLET or order.payment_id):
            raise ValidationError({"detail": "No payment was received for this order."})
        _post(order.user, kind=TxnKind.CREDIT, source=TxnSource.REFUND, amount=order.total,
              reference=f"refund:{order.request_id}", order=order,
              description=f"Refund · {order.get_service_display()} {order.customer_id}".strip())
        previous = order.status
        order.status = BillStatus.REFUNDED
        order.refunded_at = timezone.now()
        order.provider_message = f"Refunded by JobCatch: {reason}"[:255]
        order.response = {**(order.response or {}), "admin_refund": {
            "by": getattr(admin, "email", str(admin)), "at": order.refunded_at.isoformat(),
            "reason": reason, "previous_status": previous}}
        order.save(update_fields=["status", "refunded_at", "provider_message", "response", "updated_at"])
        transaction.on_commit(lambda: _notify_admin_refund(order, reason))
    logger.info("Admin refund of %s (₦%s) by %s: %s", order.request_id, order.total, admin, reason)
    return order


def _notify_admin_refund(order, reason):
    from apps.notifications.services import notify
    try:
        notify(order.user, kind="bill_alert", title=f"₦{order.total:,.2f} refunded to your wallet",
               body=f"We've refunded your {order.variation_label or order.get_service_display()} purchase"
                    f"{f' for {order.customer_id}' if order.customer_id else ''}. Reason: {reason}",
               data={"bill_order_id": str(order.id), "url": f"/bills/receipt/{order.id}"})
    except Exception:  # noqa: BLE001 — never undo a refund over a notification
        logger.exception("Refund notification failed for %s", order.request_id)


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
                                             idempotency_key=order.request_id,
                                             service=order.service)
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
    return apply_biller_status(order, result.transaction, source="lookup")


# Lookups are spaced so a run can never trip VTU.ng's burst limit (~10 failing
# requests a minute), capped per run, and older orders are checked less often.
REQUERY_SPACING_SECONDS = 7
REQUERY_MAX_PER_RUN = 15


def _requery_interval(order, now):
    """How long to wait between status checks of one order, by its age."""
    age = now - (order.sent_at or order.created_at)
    if age < timedelta(hours=1):
        return timedelta(minutes=10)
    if age < timedelta(hours=6):
        return timedelta(hours=1)
    return timedelta(hours=3)


def requery_stale_orders(min_age_minutes=2, max_age_hours=72, spacing=None, max_per_run=None):
    import time

    from django.core.cache import cache

    from .vtu import COOLDOWN_KEY

    spacing = REQUERY_SPACING_SECONDS if spacing is None else spacing
    max_per_run = REQUERY_MAX_PER_RUN if max_per_run is None else max_per_run
    now = timezone.now()
    qs = BillOrder.objects.filter(
        status__in=[BillStatus.PROCESSING, BillStatus.QUEUED],
        updated_at__lte=now - timedelta(minutes=min_age_minutes),
        created_at__gte=now - timedelta(hours=max_age_hours),
    ).order_by("created_at")
    n = 0
    for order in qs:
        if n >= max_per_run or cache.get(COOLDOWN_KEY):
            break                                   # VTU.ng is refusing us — try next run
        key = f"vtu1:requeried:{order.pk}"
        if cache.get(key):
            continue                                # checked recently enough
        if n:
            time.sleep(spacing)
        requery_order(order)
        cache.set(key, True, int(_requery_interval(order, now).total_seconds()))
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


# ------------------------------------------------------------------ gift card selling
GIFTCARD_IMAGE_TYPES = {"image/jpeg", "image/png"}
GIFTCARD_IMAGE_MAX = 2 * 1024 * 1024


def _trade_payout(provider_amount):
    keep = _pct(provider_amount, settings.GIFTCARD_SELL_MARGIN_PERCENT)
    return (Decimal(str(provider_amount)) - keep).quantize(Decimal("0.01"))


def submit_giftcard_trade(*, user, data, images=()):
    """Validate and submit a gift card for sale. Returns the GiftCardTrade."""
    from django.core.files.base import ContentFile

    from apps.common.storage import private_storage

    from .models import GiftCardTrade, GiftCardTradeStatus

    if not settings.BILL_GIFTCARD_SELL_ENABLED:
        raise ValidationError({"service": "Gift card selling is not available right now."})
    card_type = data.get("card_type")
    if card_type not in ("ecode", "physical"):
        raise ValidationError({"card_type": "Choose e-code or physical card."})
    code = (data.get("code") or "").strip()
    if card_type == "ecode" and len(code) < 10:
        raise ValidationError({"code": "Enter the full redemption code."})
    if card_type == "physical":
        if not (1 <= len(images) <= 5):
            raise ValidationError({"images": "Upload 1–5 clear photos of the card."})
        for img in images:
            if img.content_type not in GIFTCARD_IMAGE_TYPES or img.size > GIFTCARD_IMAGE_MAX:
                raise ValidationError({"images": "Photos must be JPEG or PNG, up to 2 MB each."})
    try:
        amount = Decimal(str(data.get("card_amount"))).quantize(Decimal("0.01"))
    except Exception as exc:  # noqa: BLE001
        raise ValidationError({"card_amount": "Enter the card's value."}) from exc
    if amount <= 0:
        raise ValidationError({"card_amount": "Enter the card's value."})

    trade = GiftCardTrade.objects.create(
        user=user, request_id=f"JCG{timezone.now():%y%m%d}{uuid4().hex[:16]}",
        slug=(data.get("slug") or "")[:60], card_name=(data.get("card_name") or "")[:120],
        card_country=(data.get("card_country") or "").upper()[:4],
        specific_country=(data.get("specific_country") or "")[:60], card_type=card_type,
        sub_type=(data.get("sub_type") or "")[:40],
        card_currency=(data.get("card_currency") or "").upper()[:3], card_amount=amount,
        code=code[:200] if card_type == "ecode" else "")
    files, stored = [], []
    for i, img in enumerate(images):
        content = img.read()
        files.append(("images[]", f"card-{i + 1}.{'png' if img.content_type == 'image/png' else 'jpg'}",
                      content, img.content_type))
        stored.append(private_storage().save(
            f"giftcard-trades/{user.id}/{trade.request_id}-{i + 1}"
            f".{'png' if img.content_type == 'image/png' else 'jpg'}", ContentFile(content)))
    if stored:
        trade.images = stored
        trade.save(update_fields=["images", "updated_at"])
    payload = {"slug": trade.slug, "card_country": trade.card_country, "card_type": card_type,
               "card_currency": trade.card_currency, "card_amount": str(amount),
               "payout_currency": "NGN"}
    if trade.sub_type:
        payload["sub_type"] = trade.sub_type
    if trade.specific_country:
        payload["specific_country"] = trade.specific_country
    if card_type == "ecode":
        payload["additional_info"] = code
    return _send_trade(trade, payload, files)


def _send_trade(trade, payload, files):
    from .models import GiftCardTradeStatus

    try:
        result = VTUClient().purchase("giftcard_sell", payload, path="/gift-cards/sell",
                                      idempotency_key=trade.request_id, files=files or None)
    except VTUFailed as exc:
        trade.status = GiftCardTradeStatus.FAILED
        trade.rejection_reason = str(exc)[:255]
        trade.response = {"failure": exc.body}
        trade.save(update_fields=["status", "rejection_reason", "response", "updated_at"])
        raise ValidationError({"detail": str(exc)}) from exc
    except VTUUnknown:
        logger.warning("Gift card trade %s outcome unknown; requery will check", trade.request_id)
        return trade
    return apply_trade_status(trade, result.transaction, source="submit")


def apply_trade_status(trade, tx, *, source):
    """Move a gift card trade by VTU.ng's transaction; credit the wallet once on approval."""
    from .models import GiftCardTrade, GiftCardTradeStatus
    from .vtu import transaction_status

    status = transaction_status(tx)
    order = tx.get("order") if isinstance(tx.get("order"), dict) else {}
    with transaction.atomic():
        trade = GiftCardTrade.objects.select_for_update().get(pk=trade.pk)
        if trade.status in (GiftCardTradeStatus.COMPLETED, GiftCardTradeStatus.CANCELLED):
            return trade
        trade.provider_reference = str(tx.get("reference") or trade.provider_reference)[:40]
        payout = order.get("payout_amount") or {}
        raw = payout.get("raw") if isinstance(payout, dict) else payout
        amount = (tx.get("amount") or {}).get("raw") if isinstance(tx.get("amount"), dict) else tx.get("amount")
        if raw is not None:
            trade.expected_payout = _money(raw)
        trade.response = {**(trade.response or {}), source: tx}
        if status == "completed":
            trade.provider_payout = _money(amount if amount is not None else trade.expected_payout)
            trade.user_payout = _trade_payout(trade.provider_payout)
            trade.status = GiftCardTradeStatus.COMPLETED
            trade.completed_at = timezone.now()
            trade.save()
            _post(trade.user, kind=TxnKind.CREDIT, source=TxnSource.GIFTCARD_SALE,
                  amount=trade.user_payout, reference=f"giftcard:{trade.request_id}",
                  description=f"Gift card sale · {trade.card_name or trade.slug} "
                              f"{trade.card_currency} {trade.card_amount:,.2f}")
        elif status in ("cancelled", "failed", "refunded"):
            trade.status = GiftCardTradeStatus.CANCELLED
            trade.rejection_reason = str(tx.get("cancellation_reason") or tx.get("failure_reason")
                                         or order.get("rejection_reason") or "Declined")[:255]
            trade.save()
        else:
            trade.status = GiftCardTradeStatus.PENDING
            trade.save()
            return trade
        transaction.on_commit(lambda: _notify_trade(trade))
    return trade


def requery_trade(trade):
    from .models import GiftCardTradeStatus

    if trade.status not in (GiftCardTradeStatus.SUBMITTING, GiftCardTradeStatus.PENDING):
        return trade
    try:
        result = VTUClient().get_transaction(reference=trade.provider_reference or None,
                                             idempotency_key=trade.request_id,
                                             service="giftcard_sell")
    except (VTUFailed, VTUUnknown):
        return trade
    return apply_trade_status(trade, result.transaction, source="lookup")


def requery_stale_trades(min_age_minutes=5, max_age_days=14):
    from .models import GiftCardTrade, GiftCardTradeStatus

    now = timezone.now()
    qs = GiftCardTrade.objects.filter(
        status__in=[GiftCardTradeStatus.SUBMITTING, GiftCardTradeStatus.PENDING],
        updated_at__lte=now - timedelta(minutes=min_age_minutes),
        created_at__gte=now - timedelta(days=max_age_days))
    for trade in qs:
        requery_trade(trade)
    return qs.count()


def _notify_trade(trade):
    from apps.notifications.services import notify

    from .models import GiftCardTradeStatus

    if trade.status == GiftCardTradeStatus.COMPLETED:
        notify(trade.user, kind="bill_alert", title="Gift card sale approved",
               body=f"₦{trade.user_payout:,.2f} for your {trade.card_name or trade.slug} card "
                    f"was added to your JobCatch wallet.",
               data={"giftcard_trade_id": str(trade.id), "url": "/bills?service=giftcard_sell"})
    elif trade.status == GiftCardTradeStatus.CANCELLED:
        notify(trade.user, kind="bill_alert", title="Gift card sale declined",
               body=f"Your {trade.card_name or trade.slug} card was declined: {trade.rejection_reason}",
               data={"giftcard_trade_id": str(trade.id), "url": "/bills?service=giftcard_sell"})
