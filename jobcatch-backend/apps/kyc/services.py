"""
Employer KYC workflow.

    not_started --(save details)--> in_progress --(submit)--> pending
    pending --(admin approve | auto-approve)--> verified
    pending --(admin reject)--> rejected --(edit + resubmit)--> pending
    verified --(admin revoke)--> rejected

Verification grants the "Verified Employer" badge by setting
EmployerProfile.is_cac_verified (the flag the job serializers read), and is
what unlocks job posting when KYC_REQUIRED_TO_POST_JOBS is on.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common import audit
from apps.common.phone import InvalidPhone, to_local
from apps.common.sms import SMSError, send_sms

from .models import (
    CHECK_KEYS, REQUIRED_DOCUMENTS, CheckStatus, ContactOTP, DocumentStatus, EmployerKYC,
    KYCStatus, OTPChannel,
)
from .providers import get_provider

logger = logging.getLogger(__name__)


def kyc_for(employer_profile):
    kyc, created = EmployerKYC.objects.get_or_create(
        employer=employer_profile,
        defaults={
            "registered_name": employer_profile.company_name or "",
            "cac_number": employer_profile.cac_number or "",
            "contact_email": employer_profile.user.email,
            "contact_phone": employer_profile.user.phone or "",
        },
    )
    return kyc


def is_verified_employer(employer_profile):
    return bool(employer_profile and employer_profile.is_cac_verified)


def can_post_jobs(user):
    profile = getattr(user, "employer_profile", None)
    if not profile:
        return False
    if not settings.KYC_REQUIRED_TO_POST_JOBS:
        return True
    return is_verified_employer(profile)


# ------------------------------------------------------------------ requirements
def requirements(kyc):
    """Checklist shown to the employer; submit is allowed when all are met."""
    doc_types = set(kyc.documents.exclude(status=DocumentStatus.REJECTED)
                    .values_list("doc_type", flat=True))
    fields = {
        "registered_name": kyc.registered_name, "cac_number": kyc.cac_number,
        "tin": kyc.tin, "director_name": kyc.director_name,
        "director_nin": kyc.director_nin, "business_address": kyc.business_address,
    }
    return {
        "details_complete": all(v.strip() for v in fields.values()),
        "missing_fields": [k for k, v in fields.items() if not v.strip()],
        "email_verified": kyc.is_email_verified,
        "phone_verified": kyc.is_phone_verified,
        "documents_complete": all(d in doc_types for d in REQUIRED_DOCUMENTS),
        "missing_documents": [d for d in REQUIRED_DOCUMENTS if d not in doc_types],
    }


def can_submit(kyc):
    r = requirements(kyc)
    return (kyc.is_editable and r["details_complete"] and r["email_verified"]
            and r["phone_verified"] and r["documents_complete"])


def _require_editable(kyc):
    if not kyc.is_editable:
        raise ValidationError(
            {"detail": "Your verification is under review or complete and can't be edited.",
             "code": "kyc_locked"})


def mark_in_progress(kyc, request=None):
    if kyc.status == KYCStatus.NOT_STARTED:
        kyc.status = KYCStatus.IN_PROGRESS
        kyc.save(update_fields=["status", "updated_at"])
        audit.record(kyc, "kyc.started", request=request)


# ------------------------------------------------------------------ contact OTP
def send_contact_otp(kyc, channel, request=None):
    _require_editable(kyc)
    user = kyc.user
    if channel == OTPChannel.EMAIL:
        if not kyc.contact_email:
            raise ValidationError({"contact_email": "Add a contact email first."})
        destination = kyc.contact_email.lower()
        _otp, code = ContactOTP.issue(user, channel, destination)
        try:
            send_mail(
                subject="Your JobCatch business verification code",
                message=(f"Your JobCatch verification code is {code}.\n\n"
                         f"It expires in {settings.KYC_OTP_TTL_MINUTES} minutes. "
                         f"If you didn't request it, ignore this email."),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[destination],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("KYC email OTP failed")
            raise ValidationError({"detail": "We couldn't send the email. Check the address "
                                             "and try again."}) from exc
    elif channel == OTPChannel.SMS:
        try:
            destination = to_local(kyc.contact_phone)
        except InvalidPhone as exc:
            raise ValidationError({"contact_phone": str(exc)}) from exc
        _otp, code = ContactOTP.issue(user, channel, destination)
        try:
            # Must match the template Termii approved for the sender ID word for word
            # (including "Jobcatch"), or the networks may block it.
            send_sms(destination, f"Your Jobcatch verification code is {code}. This code expires "
                                  f"in {settings.KYC_OTP_TTL_MINUTES} minutes. Do not share with anyone.")
        except SMSError as exc:
            logger.warning("KYC SMS OTP failed: %s", exc)
            raise ValidationError({"detail": "We couldn't send the SMS right now. "
                                             "Please try again shortly."}) from exc
    else:
        raise ValidationError({"channel": "Choose email or sms."})
    mark_in_progress(kyc, request)
    audit.record(kyc, f"kyc.otp_sent.{channel}", request=request)


def verify_contact_otp(kyc, channel, code, request=None):
    _require_editable(kyc)
    if channel == OTPChannel.EMAIL:
        destination = (kyc.contact_email or "").lower()
    else:
        try:
            destination = to_local(kyc.contact_phone)
        except InvalidPhone as exc:
            raise ValidationError({"contact_phone": str(exc)}) from exc
    otp = (ContactOTP.objects
           .filter(user=kyc.user, channel=channel, destination=destination, is_used=False)
           .order_by("-created_at").first())
    if not otp or not otp.verify(str(code).strip()):
        audit.record(kyc, f"kyc.otp_failed.{channel}", request=request)
        raise ValidationError({"code": "Invalid or expired code."})
    now = timezone.now()
    if channel == OTPChannel.EMAIL:
        kyc.email_verified_value, kyc.email_verified_at = destination, now
        kyc.save(update_fields=["email_verified_value", "email_verified_at", "updated_at"])
    else:
        kyc.phone_verified_value, kyc.phone_verified_at = destination, now
        kyc.save(update_fields=["phone_verified_value", "phone_verified_at", "updated_at"])
    audit.record(kyc, f"kyc.{channel}_verified", request=request, destination=destination)


# ------------------------------------------------------------------ checks
def run_automated_checks(kyc, actor=None, request=None):
    provider = get_provider()
    checks = dict(kyc.checks or {})
    for key in CHECK_KEYS:
        try:
            checks[key] = getattr(provider, f"check_{key}")(kyc)
        except Exception as exc:  # noqa: BLE001 — a provider bug must not block submission
            logger.exception("KYC check %s crashed", key)
            checks[key] = {"status": CheckStatus.ERROR, "message": f"Lookup crashed: {exc}",
                           "checked_at": timezone.now().isoformat(), "data": {}}
    kyc.checks = checks
    kyc.save(update_fields=["checks", "updated_at"])
    audit.record(kyc, "kyc.checks_run", actor=actor, request=request, provider=provider.name,
                 results={k: v["status"] for k, v in checks.items()})
    return checks


# ------------------------------------------------------------------ transitions
@transaction.atomic
def submit(kyc, request=None):
    kyc = EmployerKYC.objects.select_for_update().get(pk=kyc.pk)
    _require_editable(kyc)
    if not can_submit(kyc):
        raise ValidationError({"detail": "Complete every step before submitting.",
                               "requirements": requirements(kyc)})
    kyc.status = KYCStatus.PENDING
    kyc.submitted_at = timezone.now()
    kyc.rejection_reason = ""
    kyc.save(update_fields=["status", "submitted_at", "rejection_reason", "updated_at"])
    audit.record(kyc, "kyc.submitted", request=request)
    run_automated_checks(kyc, request=request)

    if settings.KYC_AUTO_APPROVE and kyc.all_checks_passed:
        approve(kyc, actor=None, note="Auto-approved: all automated checks passed.")
    else:
        _notify_admins(kyc)
        _notify(kyc, "Business verification submitted",
                "Thanks! We're reviewing your documents — usually within 1–2 business days.")
    kyc.refresh_from_db()   # approve() works on its own locked copy
    return kyc


def _sync_badge(kyc, verified):
    profile = kyc.employer
    profile.is_cac_verified = verified
    fields = ["is_cac_verified", "updated_at"]
    if verified:
        # The KYC record is the source of truth for the company's public identity.
        if kyc.cac_number and profile.cac_number != kyc.cac_number:
            profile.cac_number = kyc.cac_number
            fields.append("cac_number")
    profile.save(update_fields=fields)


@transaction.atomic
def approve(kyc, actor=None, note="", request=None, override=False):
    kyc = EmployerKYC.objects.select_for_update().get(pk=kyc.pk)
    kyc.status = KYCStatus.VERIFIED
    kyc.reviewed_at = timezone.now()
    kyc.reviewed_by = actor
    kyc.admin_note = note or kyc.admin_note
    kyc.rejection_reason = ""
    kyc.is_manual_override = bool(override or (actor and not kyc.all_checks_passed))
    kyc.save(update_fields=["status", "reviewed_at", "reviewed_by", "admin_note",
                            "rejection_reason", "is_manual_override", "updated_at"])
    kyc.documents.filter(status=DocumentStatus.PENDING).update(status=DocumentStatus.ACCEPTED)
    _sync_badge(kyc, True)
    audit.record(kyc, "kyc.approved", actor=actor, request=request, note=note,
                 manual_override=kyc.is_manual_override)
    _notify(kyc, "Your business is verified ✔",
            "You now carry the Verified Employer badge and can post jobs on JobCatch.")
    return kyc


@transaction.atomic
def reject(kyc, actor, reason, request=None, revoke=False):
    if not reason.strip():
        raise ValidationError({"reason": "Give the employer a reason they can act on."})
    kyc = EmployerKYC.objects.select_for_update().get(pk=kyc.pk)
    kyc.status = KYCStatus.REJECTED
    kyc.reviewed_at = timezone.now()
    kyc.reviewed_by = actor
    kyc.rejection_reason = reason
    kyc.is_manual_override = bool(revoke or kyc.all_checks_passed)
    kyc.save(update_fields=["status", "reviewed_at", "reviewed_by", "rejection_reason",
                            "is_manual_override", "updated_at"])
    _sync_badge(kyc, False)
    audit.record(kyc, "kyc.revoked" if revoke else "kyc.rejected",
                 actor=actor, request=request, reason=reason)
    _notify(kyc, "Business verification needs attention",
            f"Your verification was {'revoked' if revoke else 'not approved'}: {reason} "
            f"You can update your details and resubmit.")
    return kyc


def _notify(kyc, title, body):
    from apps.notifications.services import notify

    notify(kyc.user, kind="verification_alert", title=title, body=body,
           data={"kyc_id": str(kyc.id), "status": kyc.status, "url": "/dashboard/employer"})


def _notify_admins(kyc):
    from apps.accounts.models import User, UserRole
    from apps.notifications.models import Notification

    admins = User.objects.filter(role=UserRole.ADMIN, is_staff=True, is_active=True)
    Notification.objects.bulk_create([
        Notification(user=a, kind="admin_review", title="New employer KYC to review",
                     body=f"{kyc.registered_name or kyc.employer.company_name} submitted "
                          f"business verification.",
                     data={"kyc_id": str(kyc.id), "url": "/admin/reviews"})
        for a in admins
    ])
