"""
Engagement services: upgrade prompts, job seeker Pro, WhatsApp job alerts,
and periodic upgrade nudges.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.payments.models import PaymentPurpose
from apps.payments.services import start_checkout
from apps.subscriptions.models import VISIBILITY_PLANS, Plan, Subscription

from .models import (
    DeliveryStatus, JobAlertDelivery, JobAlertSubscription, PromptState, PromptStyle,
    Trigger, UpgradePrompt,
)

logger = logging.getLogger(__name__)

SEEKER_PRO_FEATURES = [
    "Instant WhatsApp alerts the moment a matching job is posted",
    "Pick the job categories and keywords you care about",
    "Pro badge on your applications, so employers notice you first",
    "Cancel anytime — it simply stops at the end of the month",
]


# ------------------------------------------------------------------ status helpers
def active_seeker_pro(user):
    return (Subscription.objects
            .filter(user=user, plan=Plan.SEEKER_PRO, is_active=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .order_by("-expires_at").first())


def has_visibility_plan(user):
    return Subscription.objects.filter(
        user=user, plan__in=VISIBILITY_PLANS, is_active=True
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).exists()


def _artisan_verified(user):
    profile = getattr(user, "artisan_profile", None)
    return bool(profile and profile.is_work_verified)


def _artisan_is_real(user):
    """Only nudge people who actually offer services (see apps.public docstring)."""
    profile = getattr(user, "artisan_profile", None)
    return bool(profile and profile.services.filter(is_active=True).exists())


def trigger_matches(trigger, user):
    if trigger == Trigger.SEEKER_NOT_PRO:
        return active_seeker_pro(user) is None
    if trigger == Trigger.ARTISAN_NOT_VERIFIED:
        return not _artisan_verified(user)
    if trigger == Trigger.ARTISAN_FREE_TIER:
        return _artisan_verified(user) and not has_visibility_plan(user)
    if trigger == Trigger.EMPLOYER_NOT_VERIFIED:
        profile = getattr(user, "employer_profile", None)
        return bool(profile) and not profile.is_cac_verified
    if trigger == Trigger.EMPLOYER_NO_CREDITS:
        profile = getattr(user, "employer_profile", None)
        if not (profile and profile.is_cac_verified and settings.PAID_JOB_LISTINGS_ENABLED):
            return False
        from apps.listings.services import usable_purchases

        return not usable_purchases(profile).exists()
    return False


# ------------------------------------------------------------------ prompts
def prompts_for(user, audience):
    """Prompts this user should see now, respecting dismissals and cooldowns."""
    now = timezone.now()
    prompts = list(UpgradePrompt.objects.filter(is_active=True, audience=audience))
    states = {s.prompt_id: s for s in PromptState.objects.filter(user=user, prompt__in=prompts)}
    out = []
    modal_taken = False
    for p in prompts:
        if not trigger_matches(p.trigger, user):
            continue
        st = states.get(p.id)
        cooldown = timedelta(hours=p.cooldown_hours)
        if st and st.dismissed_at and now - st.dismissed_at < cooldown:
            continue
        if p.style == PromptStyle.MODAL:
            # At most one pop-up per visit, and not more often than its cooldown.
            if modal_taken or (st and st.last_shown_at and now - st.last_shown_at < cooldown):
                continue
            modal_taken = True
        out.append(p)
    return out


def record_prompt_event(user, prompt, event):
    st, _ = PromptState.objects.get_or_create(user=user, prompt=prompt)
    now = timezone.now()
    if event == "seen":
        st.shown_count += 1
        st.last_shown_at = now
    elif event == "dismiss":
        st.dismissed_at = now
    elif event == "click":
        st.clicked_at = now
    st.save()
    return st


# ------------------------------------------------------------------ seeker Pro
def start_seeker_pro_checkout(*, user, gateway=None):
    price = settings.SEEKER_PRO_PRICE

    def link(payment):
        Subscription.objects.create(user=user, plan=Plan.SEEKER_PRO, is_active=False,
                                    started_at=timezone.now(), payment=payment)

    current = active_seeker_pro(user)
    what = "renewal" if current else "upgrade"
    return start_checkout(
        user=user, purpose=PaymentPurpose.SEEKER_PRO, amount=price,
        description=f"Job seeker Pro {what} — {settings.SEEKER_PRO_PERIOD_DAYS} days",
        gateway=gateway, before_gateway=link,
    )


# ------------------------------------------------------------------ job alerts
def _matches(sub, job):
    cat_ids = set(sub.categories.values_list("id", flat=True))
    if cat_ids and job.category_id not in cat_ids:
        return False
    words = sub.keyword_list
    if words:
        haystack = f"{job.title} {job.description[:500]}".lower()
        return any(w in haystack for w in words)
    return True


def send_job_alerts(job):
    """Notify Pro seekers whose preferences match a newly published job."""
    from apps.notifications.services import notify
    from apps.notifications.whatsapp import WhatsAppError, send_template

    if job.listing_status != "active":
        return 0
    pro_users = Subscription.objects.filter(
        plan=Plan.SEEKER_PRO, is_active=True
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).values("user_id")
    subs = (JobAlertSubscription.objects
            .filter(is_active=True, user_id__in=pro_users)
            .exclude(user=job.employer.user)
            .exclude(deliveries__job=job)
            .select_related("user").prefetch_related("categories"))
    link = f"{settings.FRONTEND_URL}/jobs/{job.id}"
    company = job.employer.company_name or "an employer"
    sent = 0
    for sub in subs:
        if not _matches(sub, job):
            continue
        status, error = DeliveryStatus.SENT, ""
        if sub.whatsapp_number:
            try:
                send_template(sub.whatsapp_number, settings.WHATSAPP_JOB_ALERT_TEMPLATE,
                              [job.title, company, link])
            except (WhatsAppError, ValueError) as exc:
                status, error = DeliveryStatus.FAILED, str(exc)[:255]
                logger.warning("WhatsApp alert failed for %s: %s", sub.user.email, exc)
        JobAlertDelivery.objects.get_or_create(subscription=sub, job=job,
                                               defaults={"status": status, "error": error})
        # In-app copy is always sent, so an alert is never lost to a WhatsApp hiccup.
        notify(sub.user, kind="job_alert", title=f"New job: {job.title}",
               body=f"{company} just posted a job that matches your alerts.",
               data={"job_id": str(job.id), "url": f"/jobs/{job.id}"})
        sub.last_sent_at = timezone.now()
        sub.save(update_fields=["last_sent_at", "updated_at"])
        sent += 1
    return sent


# ------------------------------------------------------------------ periodic nudges
NUDGE_INTERVAL = timedelta(days=7)

NUDGES = {
    "seeker_pro": ("Get job alerts on WhatsApp",
                   f"Go Pro for ₦{settings.SEEKER_PRO_PRICE:,.0f}/month and hear about matching "
                   f"jobs the moment they're posted.", "/checkout/seeker-pro"),
    "artisan_verify": ("Get the Verified Artisan badge",
                       f"Verified artisans win more bookings. Verification is a one-off "
                       f"₦{settings.ARTISAN_VERIFICATION_FEE:,.0f}.", "/dashboard/artisan?tab=verification"),
    "artisan_upgrade": ("Get seen first",
                        "Premium and Pro artisans appear at the top of search and on the home page.",
                        "/pricing"),
}


def _recently_nudged(user, key, since):
    from apps.notifications.models import Notification

    return Notification.objects.filter(user=user, kind="upgrade_nudge", data__key=key,
                                       created_at__gte=since).exists()


def send_upgrade_nudges(limit=500):
    """Weekly in-app (+email) nudges. Each user gets each nudge at most once a week."""
    from apps.accounts.models import User, UserRole
    from apps.notifications.services import notify

    since = timezone.now() - NUDGE_INTERVAL
    count = 0
    candidates = (User.objects.filter(is_active=True, is_email_verified=True,
                                      role__in=[UserRole.JOB_SEEKER, UserRole.ARTISAN])
                  .select_related("artisan_profile")[:limit])
    for user in candidates:
        if user.role == UserRole.JOB_SEEKER:
            key = "seeker_pro" if active_seeker_pro(user) is None else None
        elif _artisan_is_real(user):
            key = ("artisan_verify" if not _artisan_verified(user)
                   else "artisan_upgrade" if not has_visibility_plan(user) else None)
        else:
            key = None
        if not key or _recently_nudged(user, key, since):
            continue
        title, body, url = NUDGES[key]
        notify(user, kind="upgrade_nudge", title=title, body=body,
               data={"key": key, "url": url})
        count += 1
    return count
