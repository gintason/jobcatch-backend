"""Payment fulfilment: commission calc + post-success side effects."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.subscriptions.models import Plan, Subscription

from .models import PaymentPurpose


def compute_commission(purpose, amount):
    if purpose == PaymentPurpose.BOOKING:
        return (amount * settings.COMMISSION_RATE).quantize(Decimal("0.01"))
    return Decimal("0.00")


def fulfill_payment(payment):
    """Run side effects once a payment is confirmed successful."""
    if payment.purpose == PaymentPurpose.SUBSCRIPTION:
        _activate_subscription(payment)
    elif payment.purpose == PaymentPurpose.CV_SERVICE:
        _grant_cv_service(payment)
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
    if not sub:
        return
    now = timezone.now()
    sub.is_active = True
    sub.started_at = now
    sub.expires_at = now + timedelta(days=settings.SUBSCRIPTION_PERIOD_DAYS)
    sub.save(update_fields=["is_active", "started_at", "expires_at", "updated_at"])

    # Premium/Pro give the artisan a featured (visibility-boosted) listing.
    if sub.plan in (Plan.PREMIUM, Plan.PRO):
        profile = getattr(sub.user, "artisan_profile", None)
        if profile:
            profile.is_featured = True
            profile.save(update_fields=["is_featured", "updated_at"])
