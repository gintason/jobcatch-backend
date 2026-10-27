"""
Payment engine: checkout, confirmation, fulfilment.

  start_checkout()   -> create a Payment + invoice and get a gateway URL
  confirm_payment()  -> idempotently mark a payment successful and deliver
                        what was bought (called by webhooks, the verify
                        endpoint, and the reconciliation command)

Fulfilment is dispatched by purpose to the owning app, each handler being
idempotent. `Payment.fulfilled_at` plus a row lock guarantee value is delivered
exactly once even when the webhook and the browser redirect race.
"""
import logging
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Plan, Subscription

from .gateways import GatewayError, get_gateway
from .models import Payment, PaymentEvent, PaymentEventKind, PaymentPurpose, PaymentStatus

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    """The gateway could not start a checkout (shown to the user as 502)."""


def compute_commission(purpose, amount):
    if purpose == PaymentPurpose.BOOKING:
        return (amount * settings.COMMISSION_RATE).quantize(Decimal("0.01"))
    return Decimal("0.00")


def new_reference():
    return "JC-" + uuid4().hex[:20]


def log_event(payment, kind, *, source="", note="", payload=None):
    return PaymentEvent.objects.create(
        payment=payment, kind=kind, source=source, note=note[:255], payload=payload or {}
    )


def start_checkout(*, user, purpose, amount, description, gateway=None,
                   metadata=None, booking=None, before_gateway=None):
    """
    Create an INITIATED payment and ask the gateway for a hosted checkout URL.

    `before_gateway(payment)` lets the caller create linked rows (a pending
    subscription, a bill order...) in the same transaction as the payment, so
    a webhook can never arrive for a payment whose links don't exist yet.

    Returns (payment, authorization_url). Raises CheckoutError on gateway failure.
    """
    gateway = gateway or settings.DEFAULT_PAYMENT_GATEWAY
    gw = get_gateway(gateway)
    amount = Decimal(amount).quantize(Decimal("0.01"))

    with transaction.atomic():
        payment = Payment.objects.create(
            payer=user, purpose=purpose, booking=booking, gateway=gateway,
            reference=new_reference(), amount=amount,
            commission=compute_commission(purpose, amount),
            status=PaymentStatus.INITIATED,
            description=description[:255], metadata=metadata or {},
        )
        if before_gateway:
            before_gateway(payment)

    try:
        result = gw.initialize(
            reference=payment.reference, amount=amount, email=user.email,
            callback_url=settings.PAYMENT_CALLBACK_URL,
            name=getattr(user, "full_name", ""), description=description,
        )
    except GatewayError as exc:
        logger.error("Payment initialize failed [%s]: %s", payment.reference, exc)
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Gateway unavailable at checkout"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        log_event(payment, PaymentEventKind.ERROR, source=f"init:{gateway}", note=str(exc))
        raise CheckoutError("Payment provider is unavailable. Please try again shortly.") from exc

    log_event(payment, PaymentEventKind.INITIALIZED, source=f"init:{gateway}",
              payload={"authorization_url": result.get("authorization_url")})
    return payment, result["authorization_url"]


def confirm_payment(reference, *, source, paid_amount=None, payload=None):
    """
    Mark a payment successful and fulfil it, exactly once.

    `paid_amount` (Naira) is what the gateway says was charged; if it's lower
    than the invoice the payment is failed instead of fulfilled.
    Returns the Payment (or None if the reference is unknown).
    """
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(reference=reference).first()
        if not payment:
            return None
        if payment.status == PaymentStatus.SUCCESS and payment.fulfilled_at:
            return payment  # already done — idempotent no-op

        if payment.status != PaymentStatus.SUCCESS:
            if paid_amount is not None and Decimal(paid_amount) < payment.amount:
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = f"Underpaid: got {paid_amount}, expected {payment.amount}"
                payment.save(update_fields=["status", "failure_reason", "updated_at"])
                log_event(payment, PaymentEventKind.FAILED, source=source,
                          note=payment.failure_reason, payload=payload)
                return payment
            payment.status = PaymentStatus.SUCCESS
            payment.paid_at = timezone.now()
            if payload:
                payment.raw_response = payload
            payment.save(update_fields=["status", "paid_at", "raw_response", "updated_at"])
            log_event(payment, PaymentEventKind.SUCCEEDED, source=source)

        try:
            with transaction.atomic():   # savepoint: a failed fulfilment keeps the payment
                fulfill_payment(payment)
                payment.fulfilled_at = timezone.now()
                payment.save(update_fields=["fulfilled_at", "updated_at"])
            log_event(payment, PaymentEventKind.FULFILLED, source=source)
        except Exception as exc:  # noqa: BLE001 — logged, retried by reconcile_payments
            logger.exception("Fulfilment failed for %s", payment.reference)
            log_event(payment, PaymentEventKind.ERROR, source=source,
                      note=f"Fulfilment failed: {exc}")
    return payment


def mark_failed(payment, *, source, reason, payload=None):
    if payment.status != PaymentStatus.INITIATED:
        return payment
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason[:255]
    payment.save(update_fields=["status", "failure_reason", "updated_at"])
    log_event(payment, PaymentEventKind.FAILED, source=source, note=reason, payload=payload)
    return payment


def verify_with_gateway(payment, *, source="verify"):
    """Ask the gateway about a payment and confirm/fail it accordingly."""
    if payment.gateway not in ("paystack", "flutterwave"):
        return payment
    result = get_gateway(payment.gateway).verify(payment.reference)
    log_event(payment, PaymentEventKind.VERIFIED, source=source,
              note=result["status"], payload={"amount": str(result.get("amount"))})
    if result["status"] == "success":
        return confirm_payment(payment.reference, source=source,
                               paid_amount=result.get("amount"))
    if result["status"] == "failed":
        mark_failed(payment, source=source, reason="Gateway reported the payment failed")
    payment.refresh_from_db()
    return payment


# ------------------------------------------------------------------ fulfilment
def fulfill_payment(payment):
    """Deliver what a successful payment bought. Every handler is idempotent."""
    purpose = payment.purpose
    if purpose in (PaymentPurpose.SUBSCRIPTION, PaymentPurpose.SEEKER_PRO):
        _activate_subscription(payment)
    elif purpose == PaymentPurpose.CV_SERVICE:
        _grant_cv_service(payment)
    elif purpose == PaymentPurpose.JOB_POSTING:
        from apps.listings.services import fulfill_package_payment
        fulfill_package_payment(payment)
    elif purpose == PaymentPurpose.VERIFICATION:
        from apps.verification.services import fulfill_verification_fee
        fulfill_verification_fee(payment)
    elif purpose == PaymentPurpose.WALLET_TOPUP:
        from apps.billing.services import credit_wallet_from_payment
        credit_wallet_from_payment(payment)
    elif purpose == PaymentPurpose.BILL_PAYMENT:
        from apps.billing.services import fulfill_bill_payment
        fulfill_bill_payment(payment)
    # BOOKING payments are recorded via the Payment row; no booking mutation needed.


def _grant_cv_service(payment):
    """Unlock the one-off concierge CV service for the payer."""
    from apps.cvservice.models import CVServiceAccess

    access, _ = CVServiceAccess.objects.get_or_create(user=payment.payer)
    if access.is_active:
        return  # already unlocked — idempotent
    access.is_active = True
    access.paid_at = timezone.now()
    access.payment_reference = payment.reference
    access.save(update_fields=["is_active", "paid_at", "payment_reference", "updated_at"])


def _activate_subscription(payment):
    sub = Subscription.objects.filter(payment=payment).first()
    if not sub or sub.is_active:
        return
    now = timezone.now()
    days = (settings.SEEKER_PRO_PERIOD_DAYS if sub.plan == Plan.SEEKER_PRO
            else settings.SUBSCRIPTION_PERIOD_DAYS)
    # Renewing early stacks on top of the time left on the current plan.
    current = (Subscription.objects
               .filter(user=sub.user, plan=sub.plan, is_active=True, expires_at__gt=now)
               .exclude(pk=sub.pk).order_by("-expires_at").first())
    start_from = current.expires_at if current else now
    if current:
        current.is_active = False
        current.save(update_fields=["is_active", "updated_at"])
    sub.is_active = True
    sub.started_at = now
    sub.expires_at = start_from + timedelta(days=days)
    sub.save(update_fields=["is_active", "started_at", "expires_at", "updated_at"])

    # Premium/Pro give the artisan a featured (visibility-boosted) listing.
    if sub.plan in (Plan.PREMIUM, Plan.PRO):
        profile = getattr(sub.user, "artisan_profile", None)
        if profile and not profile.is_featured:
            profile.is_featured = True
            profile.save(update_fields=["is_featured", "updated_at"])


def reconcile_payments(now=None):
    """
    Safety net for missed webhooks:
      - INITIATED payments 10 min – 48 h old are re-verified with the gateway
      - SUCCESS payments whose fulfilment failed are fulfilled again
    """
    now = now or timezone.now()
    checked = refulfilled = 0
    stale = Payment.objects.filter(
        status=PaymentStatus.INITIATED, gateway__in=["paystack", "flutterwave"],
        created_at__lte=now - timedelta(minutes=10), created_at__gte=now - timedelta(hours=48),
    )
    for payment in stale[:200]:
        try:
            verify_with_gateway(payment, source="reconcile")
        except GatewayError as exc:
            logger.warning("Reconcile verify failed for %s: %s", payment.reference, exc)
        checked += 1
    unfulfilled = Payment.objects.filter(status=PaymentStatus.SUCCESS, fulfilled_at__isnull=True,
                                         paid_at__gte=now - timedelta(days=7))
    for payment in unfulfilled[:200]:
        confirm_payment(payment.reference, source="reconcile")
        refulfilled += 1
    # Abandoned checkouts: close them out so dashboards don't show them as pending forever.
    abandoned = Payment.objects.filter(status=PaymentStatus.INITIATED,
                                       created_at__lt=now - timedelta(hours=48))
    for payment in abandoned[:500]:
        mark_failed(payment, source="reconcile", reason="Checkout abandoned")
    return {"verified": checked, "refulfilled": refulfilled}
