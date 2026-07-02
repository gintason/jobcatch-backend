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
