"""
Wallet transfers.

* Wallet → wallet: instant, free, between two JobCatch users (email or phone).
* Wallet → bank: paid out with Paystack Transfers. The wallet is debited first
  (amount + fee); a failed or reversed transfer is refunded exactly once. The
  outcome arrives by webhook (transfer.success / failed / reversed) and is
  re-checked by `requery_stale_bank_transfers` on the scheduler.

Every transfer needs the user's 4-digit PIN. Limits: TRANSFER_MIN,
TRANSFER_MAX_SINGLE and TRANSFER_DAILY_LIMIT; money topped up by card in the
last TRANSFER_TOPUP_HOLD_HOURS can't be sent out yet (stolen-card cash-outs).
"""
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.billing.models import TxnKind, TxnSource, Wallet, WalletTransaction
from apps.billing.services import _post, wallet_for
from apps.payments.gateways import GatewayError, get_gateway

from .models import BankTransfer, BankTransferStatus, TransferPin, WalletTransfer

logger = logging.getLogger(__name__)
MAX_PIN_TRIES = 5
PIN_LOCK_MINUTES = 30
WEAK_PINS = {"0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
             "1234", "4321", "1212", "0123", "2580"}
FAILED_TRANSFER = {"failed", "reversed", "rejected", "abandoned", "blocked"}


def _d(v):
    return Decimal(str(v))


# ------------------------------------------------------------------ PIN
def has_pin(user):
    return TransferPin.objects.filter(user=user).exists()


def set_pin(user, *, pin, current_pin=None, password=None):
    pin = str(pin or "").strip()
    if not re.fullmatch(r"\d{4}", pin):
        raise ValidationError({"pin": "Your PIN must be 4 digits."})
    if pin in WEAK_PINS:
        raise ValidationError({"pin": "That PIN is too easy to guess. Choose another."})
    existing = TransferPin.objects.filter(user=user).first()
    password_ok = user.has_usable_password() and password and user.check_password(password)
    if existing:
        if current_pin:
            check_pin(user, current_pin)
        elif not password_ok:
            raise ValidationError({"password": "Enter your current PIN, or your account password to reset it."})
    elif user.has_usable_password() and not password_ok:
        raise ValidationError({"password": "Enter your account password to create a PIN."})
    TransferPin.objects.update_or_create(user=user, defaults={
        "pin_hash": make_password(pin), "failed_attempts": 0, "locked_until": None})


def check_pin(user, pin):
    """Raise ValidationError unless the PIN is right. Wrong tries persist; 5 lock for 30 min."""
    rec = TransferPin.objects.filter(user=user).first()
    if rec is None:
        raise ValidationError({"pin": "Create your transaction PIN first.", "code": "pin_required"})
    now = timezone.now()
    if rec.locked_until and rec.locked_until > now:
        mins = max(1, int((rec.locked_until - now).total_seconds() // 60))
        raise ValidationError({"pin": f"Too many wrong PINs. Try again in {mins} minute{'s' if mins > 1 else ''}.",
                               "code": "pin_locked"})
    if check_password(str(pin or ""), rec.pin_hash):
        if rec.failed_attempts or rec.locked_until:
            TransferPin.objects.filter(pk=rec.pk).update(failed_attempts=0, locked_until=None)
        return
    tries = rec.failed_attempts + 1
    locked = now + timedelta(minutes=PIN_LOCK_MINUTES) if tries >= MAX_PIN_TRIES else None
    TransferPin.objects.filter(pk=rec.pk).update(failed_attempts=0 if locked else tries, locked_until=locked)
    if locked:
        raise ValidationError({"pin": f"Too many wrong PINs. Transfers are locked for {PIN_LOCK_MINUTES} minutes.",
                               "code": "pin_locked"})
    left = MAX_PIN_TRIES - tries
    raise ValidationError({"pin": f"Incorrect PIN. {left} attempt{'s' if left > 1 else ''} left.",
                           "code": "pin_incorrect"})


# ------------------------------------------------------------------ limits
def held_amount(user):
    """Card top-ups in the hold window: spendable on bills, not transferable yet."""
    hours = settings.TRANSFER_TOPUP_HOLD_HOURS
    if hours <= 0:
        return Decimal("0")
    since = timezone.now() - timedelta(hours=hours)
    return WalletTransaction.objects.filter(
        wallet__user=user, kind=TxnKind.CREDIT, source=TxnSource.TOPUP, created_at__gte=since,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")


def transferable(user):
    balance = wallet_for(user).balance
    return max(Decimal("0"), balance - held_amount(user))


def sent_today(user):
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    w = WalletTransfer.objects.filter(sender=user, created_at__gte=start).aggregate(s=Sum("amount"))["s"] or 0
    b = (BankTransfer.objects.filter(user=user, created_at__gte=start)
         .exclude(status__in=[BankTransferStatus.FAILED, BankTransferStatus.REVERSED])
         .aggregate(s=Sum("amount"))["s"] or 0)
    return _d(w) + _d(b)


def _amount(value):
    try:
        amount = _d(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError({"amount": "Enter a valid amount."}) from exc
    lo, hi = _d(settings.TRANSFER_MIN), _d(settings.TRANSFER_MAX_SINGLE)
    if amount < lo:
        raise ValidationError({"amount": f"The minimum transfer is ₦{lo:,.0f}."})
    if amount > hi:
        raise ValidationError({"amount": f"The maximum per transfer is ₦{hi:,.0f}."})
    return amount


def _check_limits(user, amount, total):
    left = _d(settings.TRANSFER_DAILY_LIMIT) - sent_today(user)
    if amount > left:
        raise ValidationError({"amount": f"That's above your daily transfer limit. You can send ₦{max(left, 0):,.0f} more today."})
    if total > wallet_for(user).balance:
        raise ValidationError({"amount": "Insufficient wallet balance.", "code": "insufficient_funds"})
    if total > transferable(user):
        raise ValidationError({"amount": (
            f"Money added by card in the last {settings.TRANSFER_TOPUP_HOLD_HOURS} hours can be spent on "
            f"bills now and transferred after the hold. You can transfer ₦{transferable(user):,.2f} now."),
            "code": "funds_on_hold"})


def overview(user):
    daily = _d(settings.TRANSFER_DAILY_LIMIT)
    used = sent_today(user)
    pin = TransferPin.objects.filter(user=user).first()
    recent = (BankTransfer.objects.filter(user=user, status=BankTransferStatus.SUCCESSFUL)
              .order_by("-created_at").values("bank_code", "bank_name", "account_number", "account_name")[:30])
    seen, beneficiaries = set(), []
    for r in recent:
        key = (r["bank_code"], r["account_number"])
        if key not in seen:
            seen.add(key)
            beneficiaries.append(r)
    return {
        "wallet_enabled": settings.WALLET_TRANSFER_ENABLED,
        "bank_enabled": settings.BANK_TRANSFER_ENABLED and get_gateway("paystack").is_configured(),
        "has_pin": pin is not None,
        "pin_locked_until": pin.locked_until.isoformat() if pin and pin.locked_until and pin.locked_until > timezone.now() else None,
        "balance": str(wallet_for(user).balance), "transferable": str(transferable(user)),
        "on_hold": str(held_amount(user)), "hold_hours": settings.TRANSFER_TOPUP_HOLD_HOURS,
        "min": str(_d(settings.TRANSFER_MIN)), "max_single": str(_d(settings.TRANSFER_MAX_SINGLE)),
        "daily_limit": str(daily), "daily_used": str(used), "daily_left": str(max(daily - used, Decimal("0"))),
        "bank_fee": str(_d(settings.BANK_TRANSFER_FEE)),
        "beneficiaries": beneficiaries[:6],
    }


# ------------------------------------------------------------------ wallet → wallet
def _phone_variants(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 10:
        return []
    local = "0" + digits[3:] if digits.startswith("234") else digits if digits.startswith("0") else "0" + digits
    intl = "234" + local[1:]
    return [local, intl, "+" + intl]


def find_recipient(query):
    q = (query or "").strip()
    if not q:
        return None
    users = User.objects.filter(is_active=True)
    if "@" in q:
        return users.filter(email__iexact=q).first()
    variants = _phone_variants(q)
    return users.filter(phone__in=variants).first() if variants else None


def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


def send_to_wallet(sender, *, to, amount, pin, note=""):
    if not settings.WALLET_TRANSFER_ENABLED:
        raise ValidationError({"detail": "Wallet transfers are not available right now."})
    amount = _amount(amount)
    recipient = find_recipient(to)
    if recipient is None:
        raise ValidationError({"to": "No JobCatch account uses that email or phone number."})
    if recipient.pk == sender.pk:
        raise ValidationError({"to": "You can't send money to yourself."})
    check_pin(sender, pin)
    _check_limits(sender, amount, amount)
    reference = f"JCT{timezone.now():%y%m%d}{secrets.token_hex(5).upper()}"
    note = (note or "").strip()[:140]
    with transaction.atomic():
        # Lock both wallets in a fixed order so two people paying each other can't deadlock.
        ids = sorted([wallet_for(sender).pk, wallet_for(recipient).pk], key=str)
        list(Wallet.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
        _post(sender, kind=TxnKind.DEBIT, source=TxnSource.TRANSFER_OUT, amount=amount,
              reference=f"{reference}:out", description=f"To {recipient.full_name}" + (f" · {note}" if note else ""))
        _post(recipient, kind=TxnKind.CREDIT, source=TxnSource.TRANSFER_IN, amount=amount,
              reference=f"{reference}:in", description=f"From {sender.full_name}" + (f" · {note}" if note else ""))
        t = WalletTransfer.objects.create(sender=sender, recipient=recipient, amount=amount,
                                          reference=reference, note=note)
    transaction.on_commit(lambda: _notify(recipient, kind="transfer_in", title=f"You received ₦{amount:,.2f}",
                                          body=f"{sender.full_name} sent you ₦{amount:,.2f}" + (f": “{note}”" if note else ".")))
    return t


# ------------------------------------------------------------------ wallet → bank
def banks():
    cached = cache.get("transfer-banks")
    if cached:
        return cached
    try:
        items = get_gateway("paystack").list_banks()
    except GatewayError as exc:
        raise ValidationError({"detail": "Banks are temporarily unavailable. Try again shortly."}) from exc
    items = sorted(items, key=lambda b: b["name"].lower())
    cache.set("transfer-banks", items, 24 * 3600)
    return items


def resolve(bank_code, account_number):
    if not re.fullmatch(r"\d{10}", str(account_number or "")):
        raise ValidationError({"account_number": "Enter the 10-digit account number."})
    key = f"resolve:{bank_code}:{account_number}"
    name = cache.get(key)
    if name:
        return name
    try:
        name = get_gateway("paystack").resolve_account(account_number=account_number, bank_code=str(bank_code))
    except GatewayError as exc:
        raise ValidationError({"account_number": "We couldn't verify this account. Check the bank and number."}) from exc
    cache.set(key, name, 30 * 60)
    return name


def send_to_bank(user, *, bank_code, account_number, amount, pin, narration=""):
    gw = get_gateway("paystack")
    if not settings.BANK_TRANSFER_ENABLED or not gw.is_configured():
        raise ValidationError({"detail": "Bank transfers are not available right now."})
    amount = _amount(amount)
    fee = _d(settings.BANK_TRANSFER_FEE)
    bank = next((b for b in banks() if b["code"] == str(bank_code)), None)
    if bank is None:
        raise ValidationError({"bank_code": "Choose a bank."})
    account_name = resolve(bank_code, account_number)
    check_pin(user, pin)
    _check_limits(user, amount, amount + fee)
    reference = f"jcw{timezone.now():%y%m%d}{secrets.token_hex(8)}"      # Paystack: 16–50 chars, lowercase
    narration = (narration or "").strip()[:100]
    with transaction.atomic():
        bt = BankTransfer.objects.create(
            user=user, reference=reference, amount=amount, fee=fee, bank_code=bank["code"], bank_name=bank["name"],
            account_number=account_number, account_name=account_name, narration=narration)
        _post(user, kind=TxnKind.DEBIT, source=TxnSource.WITHDRAWAL, amount=amount + fee, reference=f"{reference}:debit",
              description=f"To {account_name} · {bank['name']} {account_number[-4:]}"
                          + (f" (incl. ₦{fee:,.0f} fee)" if fee else ""))
    return _initiate(bt)


def _initiate(bt):
    """Create the recipient (once) and ask Paystack to pay. Safe to repeat: the reference is unique."""
    gw = get_gateway("paystack")
    try:
        if not bt.recipient_code:
            bt.recipient_code = gw.create_transfer_recipient(
                name=bt.account_name, account_number=bt.account_number, bank_code=bt.bank_code)
            bt.save(update_fields=["recipient_code", "updated_at"])
        result = gw.initiate_transfer(amount=bt.amount, recipient_code=bt.recipient_code, reference=bt.reference,
                                      reason=bt.narration or f"JobCatch wallet withdrawal {bt.reference[-6:]}")
    except GatewayError as exc:
        if exc.http_status and 400 <= exc.http_status < 500 and exc.http_status not in (408, 409, 429):
            msg = str(exc)
            if "duplicate" in msg.lower():       # already accepted on an earlier try — just check it
                return requery(bt)
            logger.warning("Paystack refused transfer %s: %s", bt.reference, msg)
            return _finish(bt, "failed", reason=_friendly(msg), raw={"error": msg[:500]}, source="initiate")
        logger.warning("Transfer %s outcome unknown, will re-check: %s", bt.reference, exc)
        return bt
    if result["transfer_code"]:
        BankTransfer.objects.filter(pk=bt.pk).update(transfer_code=result["transfer_code"][:60])
    if result["status"] == "otp":
        # The Paystack account still asks for an OTP per transfer; it must be disabled for automatic payouts.
        logger.error("Paystack transfer %s needs OTP — disable transfer OTP in the Paystack dashboard", bt.reference)
        return _finish(bt, "failed", reason="Bank transfers are temporarily unavailable.", raw=result["raw"], source="initiate")
    return _finish(bt, result["status"], raw=result["raw"], source="initiate")


def _friendly(msg):
    m = msg.lower()
    if "balance" in m:
        return "Bank transfers are temporarily unavailable. Your money has been refunded."
    if "account" in m and ("invalid" in m or "resolve" in m):
        return "The bank rejected this account. Your money has been refunded."
    return "The transfer could not be completed. Your money has been refunded."


def _finish(bt, gateway_status, *, reason="", raw=None, source=""):
    """Apply Paystack's status. Refunds failed/reversed transfers exactly once."""
    gateway_status = (gateway_status or "").lower()
    with transaction.atomic():
        bt = BankTransfer.objects.select_for_update().get(pk=bt.pk)
        bt.response = {**(bt.response or {}), source or "update": raw or {}}
        if gateway_status == "success" and bt.status == BankTransferStatus.PROCESSING:
            bt.status, bt.completed_at = BankTransferStatus.SUCCESSFUL, timezone.now()
            bt.save(update_fields=["status", "completed_at", "response", "updated_at"])
            transaction.on_commit(lambda: _notify(bt.user, kind="transfer_out", title="Transfer successful",
                                                  body=f"₦{bt.amount:,.2f} sent to {bt.account_name} ({bt.bank_name})."))
            return bt
        if gateway_status in FAILED_TRANSFER and bt.status in (BankTransferStatus.PROCESSING, BankTransferStatus.SUCCESSFUL):
            reversed_ = gateway_status == "reversed" or bt.status == BankTransferStatus.SUCCESSFUL
            bt.status = BankTransferStatus.REVERSED if reversed_ else BankTransferStatus.FAILED
            bt.failure_reason = (reason or "The bank could not complete this transfer.")[:255]
            bt.completed_at = timezone.now()
            bt.save(update_fields=["status", "failure_reason", "completed_at", "response", "updated_at"])
            _post(bt.user, kind=TxnKind.CREDIT, source=TxnSource.REFUND, amount=bt.total,
                  reference=f"{bt.reference}:refund", description=f"Refund · transfer to {bt.account_name}")
            transaction.on_commit(lambda: _notify(bt.user, kind="transfer_out", title="Transfer failed — refunded",
                                                  body=f"₦{bt.total:,.2f} is back in your wallet. {bt.failure_reason}"))
            return bt
        bt.save(update_fields=["response", "updated_at"])
        return bt


def requery(bt):
    gw = get_gateway("paystack")
    try:
        result = gw.verify_transfer(bt.reference)
    except GatewayError as exc:
        if exc.http_status in (400, 404):
            # Paystack never received it: retry with the same reference for a while, then refund.
            if bt.created_at > timezone.now() - timedelta(minutes=30):
                return _initiate(bt)
            return _finish(bt, "failed", reason="The transfer could not be started. Your money has been refunded.",
                           raw={"error": str(exc)[:300]}, source="requery")
        logger.warning("Transfer requery failed for %s: %s", bt.reference, exc)
        return bt
    return _finish(bt, result["status"], reason=result["reason"], raw=result["raw"], source="requery")


def requery_stale_bank_transfers():
    cutoff = timezone.now() - timedelta(minutes=2)
    done = 0
    for bt in BankTransfer.objects.filter(status=BankTransferStatus.PROCESSING, created_at__lte=cutoff,
                                          created_at__gte=timezone.now() - timedelta(days=7))[:100]:
        requery(bt)
        done += 1
    return done


def handle_paystack_transfer_event(payload):
    """Signed transfer.success / transfer.failed / transfer.reversed webhook."""
    data = payload.get("data") or {}
    bt = BankTransfer.objects.filter(reference=str(data.get("reference") or "")).first()
    if bt is None:
        return None
    event = str(payload.get("event") or "")
    status = {"transfer.success": "success", "transfer.failed": "failed", "transfer.reversed": "reversed"}.get(event, "")
    return _finish(bt, status, reason=str(data.get("reason") or ""), raw={"event": event, "status": data.get("status")},
                   source="webhook")


# ------------------------------------------------------------------ history
def history(user, limit=50):
    rows = []
    for t in WalletTransfer.objects.filter(Q(sender=user) | Q(recipient=user)).select_related("sender", "recipient")[:limit]:
        out = t.sender_id == user.pk
        other = t.recipient if out else t.sender
        rows.append({"id": str(t.pk), "type": "wallet_out" if out else "wallet_in", "reference": t.reference,
                     "counterparty": other.full_name, "detail": "JobCatch wallet", "amount": str(t.amount), "fee": "0.00",
                     "status": "successful", "status_label": "Successful", "note": t.note,
                     "created_at": t.created_at.isoformat()})
    for b in BankTransfer.objects.filter(user=user)[:limit]:
        rows.append(bank_row(b))
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows[:limit]


def bank_row(b):
    return {"id": str(b.pk), "type": "bank", "reference": b.reference, "counterparty": b.account_name,
            "detail": f"{b.bank_name} · {b.account_number}", "amount": str(b.amount), "fee": str(b.fee),
            "status": b.status, "status_label": b.get_status_display(), "note": b.narration,
            "failure_reason": b.failure_reason, "created_at": b.created_at.isoformat(),
            "completed_at": b.completed_at.isoformat() if b.completed_at else None}


def _notify(user, *, kind, title, body):
    from apps.notifications.services import notify
    try:
        notify(user, kind=kind, title=title, body=body)
    except Exception:  # noqa: BLE001
        logger.exception("Transfer notification failed")
