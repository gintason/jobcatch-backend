"""
Listing credits: buy a package, spend a credit to publish a job, expire.

    purchase_package()        -> free: active immediately; paid: checkout URL
    fulfill_package_payment() -> payment confirmed: activate credits and, if
                                 the checkout was for a specific job, publish
                                 it instantly
    publish_job()             -> spend one credit, job live for listing_days
    expire_listings()         -> cron: close expired jobs + stale credit packs
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.jobs.models import Job
from apps.jobs.signals import job_published
from apps.payments.models import PaymentPurpose
from apps.payments.services import start_checkout

from .models import PackagePurchase, PurchaseStatus

logger = logging.getLogger(__name__)


class PaymentRequired(ValidationError):
    status_code = 402
    default_code = "payment_required"


def usable_purchases(employer):
    now = timezone.now()
    return (PackagePurchase.objects
            .filter(employer=employer, status=PurchaseStatus.ACTIVE,
                    credits_used__lt=F("credits_total"))
            .exclude(expires_at__lte=now)
            # Spend the credits that expire soonest first; featured last so a
            # standard job doesn't burn a featured credit by accident.
            .order_by("is_featured", F("expires_at").asc(nulls_last=True), "created_at"))


def credit_summary(employer):
    purchases = list(usable_purchases(employer).select_related("package"))
    return {
        "total_credits": sum(p.credits_remaining for p in purchases),
        "featured_credits": sum(p.credits_remaining for p in purchases if p.is_featured),
        "purchases": purchases,
    }


def _check_limit(employer, package):
    if package.limit_per_employer is None:
        return
    used = (PackagePurchase.objects.filter(employer=employer, package=package)
            .exclude(status__in=[PurchaseStatus.PENDING, PurchaseStatus.CANCELLED]).count())
    if used >= package.limit_per_employer:
        raise ValidationError({"detail": f"You've already used the {package.name} package."})


def _new_purchase(employer, package, payment=None):
    return PackagePurchase.objects.create(
        employer=employer, package=package, payment=payment,
        status=PurchaseStatus.PENDING, price=package.price,
        credits_total=package.job_credits, listing_days=package.listing_days,
        is_featured=package.is_featured, validity_days=package.validity_days,
    )


def _activate(purchase):
    if purchase.status != PurchaseStatus.PENDING:
        return purchase
    now = timezone.now()
    purchase.status = PurchaseStatus.ACTIVE
    purchase.activated_at = now
    purchase.expires_at = now + timedelta(days=purchase.validity_days) if purchase.validity_days else None
    purchase.save(update_fields=["status", "activated_at", "expires_at", "updated_at"])
    return purchase


def _job_to_publish(employer, job_id):
    if not job_id:
        return None
    job = Job.objects.filter(pk=job_id, employer=employer).first()
    if not job:
        raise ValidationError({"job": "Job not found."})
    return job


def purchase_package(*, user, package, gateway=None, job_id=None):
    """Returns {"purchase", "authorization_url"|None, "job"|None, "payment"|None}."""
    employer = user.employer_profile
    if not package.is_active:
        raise ValidationError({"detail": "This package is no longer available."})
    _check_limit(employer, package)
    job = _job_to_publish(employer, job_id)

    if package.price <= 0:
        with transaction.atomic():
            purchase = _activate(_new_purchase(employer, package))
            if job and job.listing_status in ("draft", "expired"):
                job = publish_job(job, purchase=purchase)
        return {"purchase": purchase, "authorization_url": None, "job": job, "payment": None}

    holder = {}

    def link(payment):
        holder["purchase"] = _new_purchase(employer, package, payment=payment)

    payment, url = start_checkout(
        user=user, purpose=PaymentPurpose.JOB_POSTING, amount=package.price,
        description=f"{package.name} — {package.job_credits} job listing"
                    f"{'s' if package.job_credits != 1 else ''}",
        gateway=gateway, before_gateway=link,
        metadata={"package_id": str(package.id), "package": package.name,
                  "job_id": str(job.id) if job else None},
    )
    return {"purchase": holder["purchase"], "authorization_url": url, "job": job,
            "payment": payment}


def fulfill_package_payment(payment):
    """Payment confirmed: activate the credits, then publish the job it was for."""
    purchase = PackagePurchase.objects.select_for_update().filter(payment=payment).first()
    if not purchase:
        logger.error("Package payment %s has no purchase row", payment.reference)
        return
    _activate(purchase)
    job_id = (payment.metadata or {}).get("job_id")
    if job_id:
        job = Job.objects.filter(pk=job_id, employer=purchase.employer).first()
        if job and job.listing_status in ("draft", "expired") and purchase.is_usable:
            publish_job(job, purchase=purchase)


@transaction.atomic
def publish_job(job, *, purchase=None, featured=None):
    """
    Make a draft (or expired) job live.

    With paid listings on, spends one credit from `purchase` (or the best
    usable purchase). `featured=True` insists on a featured credit.
    """
    job = Job.objects.select_for_update().get(pk=job.pk)
    status = job.listing_status
    if status in ("active",):
        return job
    if status == "closed":
        raise ValidationError({"detail": "Reopen this job instead of republishing it."})

    from apps.kyc.services import can_post_jobs

    if not can_post_jobs(job.employer.user):
        raise ValidationError({"detail": "Verify your business before publishing jobs.",
                               "code": "kyc_required"})

    now = timezone.now()
    if not settings.PAID_JOB_LISTINGS_ENABLED:
        job.published_at, job.expires_at, job.is_open = now, None, True
        job.save(update_fields=["published_at", "expires_at", "is_open", "updated_at"])
        transaction.on_commit(lambda: job_published.send(sender=Job, job=job))
        return job

    if purchase is None:
        qs = usable_purchases(job.employer)
        if featured is True:
            qs = qs.filter(is_featured=True)
        elif featured is False:
            qs = qs.filter(is_featured=False)
        purchase = qs.first()
    if purchase is not None:
        purchase = PackagePurchase.objects.select_for_update().get(pk=purchase.pk)
    if purchase is None or not purchase.is_usable or purchase.employer_id != job.employer_id:
        raise PaymentRequired({"detail": "Buy a listing package to publish this job.",
                               "code": "payment_required"})

    purchase.credits_used += 1
    if purchase.credits_remaining == 0:
        purchase.status = PurchaseStatus.EXHAUSTED
    purchase.save(update_fields=["credits_used", "status", "updated_at"])

    job.published_at = now
    job.expires_at = now + timedelta(days=purchase.listing_days)
    job.is_featured = purchase.is_featured
    job.is_open = True
    job.listing_purchase = purchase
    job.save(update_fields=["published_at", "expires_at", "is_featured", "is_open",
                            "listing_purchase", "updated_at"])
    transaction.on_commit(lambda: job_published.send(sender=Job, job=job))
    _notify_published(job)
    return job


def _notify_published(job):
    from apps.notifications.services import notify

    notify(job.employer.user, kind="job_alert", title="Your job is live",
           body=f"'{job.title}' is now visible to job seekers until "
                f"{timezone.localtime(job.expires_at):%d %b %Y}.",
           data={"job_id": str(job.id), "url": f"/jobs/{job.id}"})


def expire_listings(now=None):
    """Close jobs past their listing period and expire stale credit packs. Idempotent."""
    from apps.notifications.services import notify

    now = now or timezone.now()
    expired_jobs = list(Job.objects.filter(is_open=True, expires_at__lte=now)
                        .select_related("employer__user"))
    for job in expired_jobs:
        Job.objects.filter(pk=job.pk).update(is_open=False, updated_at=now)
        notify(job.employer.user, kind="job_alert", title="Job listing expired",
               body=f"'{job.title}' has reached the end of its listing period. "
                    f"Republish it from your dashboard to keep receiving applicants.",
               data={"job_id": str(job.id), "url": "/dashboard/employer"})

    stale = PackagePurchase.objects.filter(status=PurchaseStatus.ACTIVE, expires_at__lte=now)
    stale_count = stale.update(status=PurchaseStatus.EXPIRED, updated_at=now)
    return {"jobs_expired": len(expired_jobs), "packages_expired": stale_count}


def reopen_check(job):
    """Closed (by the employer) but still within its paid period may reopen for free."""
    return bool(job.published_at and (job.expires_at is None or job.expires_at > timezone.now()))
