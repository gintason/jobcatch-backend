"""
Verification side effects.

Approving a verification grants the matching badge:
  identity / gov_id / image -> user.is_identity_verified
  cac                       -> employer_profile.is_cac_verified
  video                     -> artisan_profile.is_work_verified
Artisan job-sample videos (a separate model) grant is_work_verified on approval too.
"""
from .models import VerificationType


def apply_verification_badge(verification):
    user = verification.user
    t = verification.type

    if t in (VerificationType.IDENTITY, VerificationType.GOV_ID, VerificationType.IMAGE):
        if not user.is_identity_verified:
            user.is_identity_verified = True
            user.save(update_fields=["is_identity_verified", "updated_at"])

    elif t == VerificationType.CAC:
        profile = getattr(user, "employer_profile", None)
        if profile and not profile.is_cac_verified:
            profile.is_cac_verified = True
            profile.save(update_fields=["is_cac_verified", "updated_at"])

    elif t == VerificationType.VIDEO:
        profile = getattr(user, "artisan_profile", None)
        if profile and not profile.is_work_verified:
            profile.is_work_verified = True
            profile.save(update_fields=["is_work_verified", "updated_at"])


def approve_job_video(video, admin, note=""):
    """Approve an artisan's job-sample video and grant the work-verified badge."""
    from apps.accounts.models import JobVideoStatus

    video.status = JobVideoStatus.APPROVED
    video.reviewed_by = admin
    video.review_note = note
    video.save(update_fields=["status", "reviewed_by", "review_note", "updated_at"])

    profile = video.artisan
    if not profile.is_work_verified:
        profile.is_work_verified = True
        profile.save(update_fields=["is_work_verified", "updated_at"])


def reject_job_video(video, admin, note=""):
    from apps.accounts.models import JobVideoStatus

    video.status = JobVideoStatus.REJECTED
    video.reviewed_by = admin
    video.review_note = note
    video.save(update_fields=["status", "reviewed_by", "review_note", "updated_at"])


# ---------------------------------------------------------------------------
# Paid artisan verification (₦5,000)
#
#   draft --submit--> awaiting_payment --fee paid--> pending_review
#   pending_review --approve--> verified   (Verified Artisan badge)
#   pending_review --reject---> rejected --edit + resubmit--> pending_review
#
# The fee is paid once per application: resubmitting after a rejection does
# not charge again.
# ---------------------------------------------------------------------------
def application_for(artisan_profile):
    from .models import ArtisanVerificationApplication

    app, _ = ArtisanVerificationApplication.objects.get_or_create(artisan=artisan_profile)
    return app


def application_requirements(app):
    from .models import ArtisanDocumentType

    doc_types = set(app.documents.values_list("doc_type", flat=True))
    return {
        "categories": app.categories.exists(),
        "experience": app.years_experience is not None,
        "identity": bool(app.id_type and app.id_number.strip()),
        "government_id_document": ArtisanDocumentType.GOVERNMENT_ID in doc_types,
    }


def submit_application(app, *, user, gateway=None, request=None):
    """
    Returns {"application", "authorization_url"|None}. Unpaid applications go
    to checkout; paid ones go straight into the review queue.
    """
    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone
    from rest_framework.exceptions import ValidationError

    from apps.common import audit
    from apps.payments.models import PaymentPurpose
    from apps.payments.services import start_checkout

    from .models import ArtisanVerificationApplication, ArtisanVerificationStatus

    if not app.is_editable:
        raise ValidationError({"detail": "Your verification is already under review or complete."})
    missing = [k for k, ok in application_requirements(app).items() if not ok]
    if missing:
        raise ValidationError({"detail": "Complete your application before submitting.",
                               "missing": missing})

    if app.fee_paid:
        with transaction.atomic():
            app = ArtisanVerificationApplication.objects.select_for_update().get(pk=app.pk)
            app.status = ArtisanVerificationStatus.PENDING_REVIEW
            app.submitted_at = timezone.now()
            app.save(update_fields=["status", "submitted_at", "updated_at"])
        audit.record(app, "artisan_verification.submitted", request=request, fee="already_paid")
        _notify_admins_artisan(app)
        return {"application": app, "authorization_url": None}

    fee = settings.ARTISAN_VERIFICATION_FEE

    def link(payment):
        ArtisanVerificationApplication.objects.filter(pk=app.pk).update(
            status=ArtisanVerificationStatus.AWAITING_PAYMENT, payment=payment, fee_amount=fee,
            updated_at=timezone.now(),
        )

    payment, url = start_checkout(
        user=user, purpose=PaymentPurpose.VERIFICATION, amount=fee,
        description="Artisan verification fee (Verified Artisan badge review)",
        gateway=gateway, before_gateway=link,
        metadata={"application_id": str(app.id)},
    )
    audit.record(app, "artisan_verification.checkout_started", request=request,
                 reference=payment.reference)
    app.refresh_from_db()
    return {"application": app, "authorization_url": url}


def fulfill_verification_fee(payment):
    """Verification fee confirmed: queue the application for review."""
    from django.utils import timezone

    from apps.common import audit

    from .models import ArtisanVerificationApplication, ArtisanVerificationStatus

    app_id = (payment.metadata or {}).get("application_id")
    app = (ArtisanVerificationApplication.objects.select_for_update()
           .filter(pk=app_id).first() if app_id else None)
    if not app:
        return
    if app.fee_paid_at:
        return  # idempotent
    app.fee_paid_at = timezone.now()
    app.payment = payment
    app.fee_amount = payment.amount
    if app.status in (ArtisanVerificationStatus.DRAFT, ArtisanVerificationStatus.AWAITING_PAYMENT):
        app.status = ArtisanVerificationStatus.PENDING_REVIEW
        app.submitted_at = timezone.now()
    app.save(update_fields=["fee_paid_at", "payment", "fee_amount", "status",
                            "submitted_at", "updated_at"])
    audit.record(app, "artisan_verification.fee_paid", actor=payment.payer,
                 reference=payment.reference, amount=str(payment.amount))
    _notify_artisan(app, "Verification fee received",
                    "Thanks! Your documents are now with our review team — usually 1–2 business days.")
    _notify_admins_artisan(app)


def review_application(app, *, admin, approve, note="", request=None):
    from django.db import transaction
    from django.utils import timezone
    from rest_framework.exceptions import ValidationError

    from apps.common import audit

    from .models import ArtisanVerificationApplication, ArtisanVerificationStatus

    if not approve and not note.strip():
        raise ValidationError({"note": "Tell the artisan why, so they can fix it."})
    with transaction.atomic():
        app = ArtisanVerificationApplication.objects.select_for_update().get(pk=app.pk)
        app.status = (ArtisanVerificationStatus.VERIFIED if approve
                      else ArtisanVerificationStatus.REJECTED)
        app.reviewed_by = admin
        app.reviewed_at = timezone.now()
        app.review_note = note
        app.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        profile = app.artisan
        user = profile.user
        if approve:
            if not profile.is_work_verified:
                profile.is_work_verified = True
                profile.save(update_fields=["is_work_verified", "updated_at"])
            if not user.is_identity_verified:
                user.is_identity_verified = True
                user.save(update_fields=["is_identity_verified", "updated_at"])
    audit.record(app, "artisan_verification.approved" if approve else "artisan_verification.rejected",
                 actor=admin, request=request, note=note)
    if approve:
        _notify_artisan(app, "You're a Verified Artisan ✔",
                        "Your Verified Artisan badge is now live on your profile and listings.")
    else:
        _notify_artisan(app, "Verification needs attention",
                        f"We couldn't verify you yet: {note} Update your application and resubmit "
                        f"— no extra fee.")
    return app


def revoke_artisan_verification(app, *, admin, note, request=None):
    from apps.common import audit

    from .models import ArtisanVerificationStatus

    app.status = ArtisanVerificationStatus.REJECTED
    app.review_note = note
    app.reviewed_by = admin
    app.save(update_fields=["status", "review_note", "reviewed_by", "updated_at"])
    profile = app.artisan
    profile.is_work_verified = False
    profile.save(update_fields=["is_work_verified", "updated_at"])
    audit.record(app, "artisan_verification.revoked", actor=admin, request=request, note=note)
    _notify_artisan(app, "Verified badge removed", f"Your Verified Artisan badge was removed: {note}")


def _notify_artisan(app, title, body):
    from apps.notifications.services import notify

    notify(app.artisan.user, kind="verification_alert", title=title, body=body,
           data={"application_id": str(app.id), "status": app.status,
                 "url": "/dashboard/artisan"})


def _notify_admins_artisan(app):
    from apps.accounts.models import User, UserRole
    from apps.notifications.models import Notification

    admins = User.objects.filter(role=UserRole.ADMIN, is_staff=True, is_active=True)
    Notification.objects.bulk_create([
        Notification(user=a, kind="admin_review", title="Artisan verification to review",
                     body=f"{app.artisan.user.full_name} paid the verification fee.",
                     data={"application_id": str(app.id), "url": "/admin/reviews"})
        for a in admins
    ])
