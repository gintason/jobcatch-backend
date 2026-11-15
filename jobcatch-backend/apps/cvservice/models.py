"""
Paid CV service.

A job seeker pays a one-off fee (see settings.CV_SERVICE_PRICE, default
₦5,000) to unlock a concierge service: they submit their CV to the JobCatch
admin, who reviews it and forwards selected CVs to employers.

Three pieces:
  CVServiceAccess  - the entitlement, flipped on by a successful payment
  CVSubmission     - a CV the seeker sent to the JobCatch admin
  CVReferral       - the admin forwarding one submission to one employer
"""
import uuid

from django.conf import settings
from django.db import models


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CVServiceAccess(TimeStampedUUIDModel):
    """One-off entitlement to the concierge CV service."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cv_service_access",
    )
    is_active = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "CV service access"
        verbose_name_plural = "CV service access"

    def __str__(self):
        state = "active" if self.is_active else "inactive"
        return f"{self.user.email} ({state})"


class SubmissionStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    REVIEWED = "reviewed", "Reviewed"
    FORWARDED = "forwarded", "Forwarded to employer(s)"


class CVSubmission(TimeStampedUUIDModel):
    """A CV a paying job seeker sent to the JobCatch admin."""

    seeker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cv_submissions",
    )
    cv_file = models.FileField(upload_to="cv_service/")
    headline = models.CharField(max_length=180, blank=True, default="")
    note = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING, db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.seeker.email} - {self.headline or 'CV'}"


class CVReferral(TimeStampedUUIDModel):
    """The JobCatch admin forwarding a submitted CV to an employer."""

    submission = models.ForeignKey(
        CVSubmission, on_delete=models.CASCADE, related_name="referrals"
    )
    employer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referred_cvs",
        help_text="The employer account this CV was sent to.",
    )
    admin_note = models.TextField(
        blank=True, default="",
        help_text="Optional message shown to the employer alongside the CV.",
    )

    class Meta:
        ordering = ("-created_at",)
        unique_together = ("submission", "employer")

    def __str__(self):
        return f"{self.submission.seeker.email} -> {self.employer.email}"
