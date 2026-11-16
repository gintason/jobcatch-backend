"""Subscription expiry handling + renewal reminders (run from the scheduler)."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import VISIBILITY_PLANS, Plan, Subscription

RENEW_URL = {Plan.SEEKER_PRO: "/checkout/seeker-pro"}


def expire_subscriptions(now=None):
    """Deactivate lapsed plans and drop the featured flag they paid for."""
    from apps.notifications.services import notify

    now = now or timezone.now()
    lapsed = list(Subscription.objects.filter(is_active=True, expires_at__lte=now)
                  .select_related("user"))
    for sub in lapsed:
        sub.is_active = False
        sub.save(update_fields=["is_active", "updated_at"])
        user = sub.user
        if sub.plan in VISIBILITY_PLANS:
            still_paid = Subscription.objects.filter(
                user=user, plan__in=VISIBILITY_PLANS, is_active=True
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()
            profile = getattr(user, "artisan_profile", None)
            if profile and profile.is_featured and not still_paid:
                profile.is_featured = False
                profile.save(update_fields=["is_featured", "updated_at"])
        notify(user, kind="subscription_alert", title=f"Your {sub.get_plan_display()} plan has ended",
               body="Renew any time to get your benefits back.",
               data={"subscription_id": str(sub.id), "url": RENEW_URL.get(sub.plan, "/pricing")})
    return len(lapsed)


def send_renewal_reminders(days_before=3, now=None):
    """One reminder per subscription, when it enters its last `days_before` days."""
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    now = now or timezone.now()
    due = Subscription.objects.filter(is_active=True, expires_at__gt=now,
                                      expires_at__lte=now + timedelta(days=days_before))
    sent = 0
    for sub in due.select_related("user"):
        already = Notification.objects.filter(user=sub.user, kind="subscription_alert",
                                              data__reminder_for=str(sub.id)).exists()
        if already:
            continue
        notify(sub.user, kind="subscription_alert",
               title=f"Your {sub.get_plan_display()} plan ends soon",
               body=f"It expires on {timezone.localtime(sub.expires_at):%d %b %Y}. Renew now "
                    f"and the new period is added on top of the days you have left.",
               data={"reminder_for": str(sub.id), "url": RENEW_URL.get(sub.plan, "/pricing")})
        sent += 1
    return sent
