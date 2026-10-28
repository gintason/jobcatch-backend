"""
Complaint box: anyone (signed in or not) can report a problem — an artisan,
wrong billing, an employer/job, a payment — and JobCatch tracks it to a
resolution. Each complaint gets a reference the complainant can quote and use
to check progress.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.common.storage import private_storage
from apps.common.validators import validate_document_upload


class ComplaintCategory(models.TextChoices):
    ARTISAN = "artisan", "An artisan"
    BILLING = "billing", "Wrong billing / bill payment"
    PAYMENT = "payment", "A payment or refund"
    EMPLOYER = "employer", "An employer or job listing"
    OTHER = "other", "Something else"


class ComplaintStatus(models.TextChoices):
    OPEN = "open", "Received"
    IN_REVIEW = "in_review", "Being reviewed"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


def generate_reference():
    return f"JCC-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}"


def complaint_upload_to(instance, filename):
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"complaints/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class Complaint(BaseModel):
    reference = models.CharField(max_length=30, unique=True, default=generate_reference)
    category = models.CharField(max_length=20, choices=ComplaintCategory.choices, db_index=True)

    # --- who is complaining (always captured, even when signed in) ---
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="complaints")
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    # --- what it's about ---
    subject = models.CharField(max_length=150)
    description = models.TextField()
    incident_date = models.DateField(null=True, blank=True)
    artisan = models.ForeignKey("accounts.ArtisanProfile", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="complaints")
    artisan_name = models.CharField(max_length=150, blank=True)
    # Receipt / payment reference / invoice number the complaint relates to.
    transaction_reference = models.CharField(max_length=100, blank=True)
    attachment = models.FileField(storage=private_storage, upload_to=complaint_upload_to,
                                  validators=[validate_document_upload], max_length=255,
                                  null=True, blank=True)

    # --- handling ---
    status = models.CharField(max_length=20, choices=ComplaintStatus.choices,
                              default=ComplaintStatus.OPEN, db_index=True)
    admin_response = models.TextField(blank=True, help_text="Sent to the complainant by email.")
    internal_note = models.TextField(blank=True, help_text="Staff only — never shown to the complainant.")
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="complaints_handled")
    responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference} · {self.get_category_display()} · {self.status}"
