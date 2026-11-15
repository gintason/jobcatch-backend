"""Identity / document verification workflow feeding the 'verified badge'."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.common.storage import artisan_document_upload_to, private_storage
from apps.common.validators import validate_document_upload, validate_upload


class VerificationType(models.TextChoices):
    IDENTITY = "identity", "Identity"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    GOV_ID = "gov_id", "Government ID"
    CAC = "cac", "CAC (employer)"


class VerificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Verification(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="verifications"
    )
    type = models.CharField(max_length=20, choices=VerificationType.choices)
    # Stored in a PRIVATE bucket; served only via signed URLs (production settings).
    document = models.FileField(upload_to="verifications/", validators=[validate_upload])
    status = models.CharField(
        max_length=20, choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING, db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="verifications_reviewed",
    )
    review_note = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.type} · {self.status} · {self.user.email}"


# ---------------------------------------------------------------------------
# Paid artisan verification (₦5,000) -> "Verified Artisan" badge
# ---------------------------------------------------------------------------


class ArtisanVerificationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting verification fee"
    PENDING_REVIEW = "pending_review", "Under review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class IDType(models.TextChoices):
    NIN = "nin", "National ID (NIN slip / card)"
    VOTERS_CARD = "voters_card", "Voter's card"
    DRIVERS_LICENCE = "drivers_licence", "Driver's licence"
    PASSPORT = "intl_passport", "International passport"


class ArtisanVerificationApplication(BaseModel):
    """
    Standardised artisan onboarding: identity, trade credentials and skill
    categories, plus the one-off verification fee. Approval grants the
    "Verified Artisan" badge (ArtisanProfile.is_work_verified + identity flag).
    """

    artisan = models.OneToOneField(
        "accounts.ArtisanProfile", on_delete=models.CASCADE, related_name="verification_application"
    )
    status = models.CharField(max_length=20, choices=ArtisanVerificationStatus.choices,
                              default=ArtisanVerificationStatus.DRAFT, db_index=True)
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+")
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    id_type = models.CharField(max_length=20, choices=IDType.choices, blank=True)
    id_number = models.CharField(max_length=30, blank=True)
    trade_credentials = models.TextField(
        blank=True, help_text="Certificates, apprenticeship, trade association membership…"
    )
    # --- fee ---
    fee_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    fee_paid_at = models.DateTimeField(null=True, blank=True)
    payment = models.ForeignKey("payments.Payment", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    # --- review ---
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    review_note = models.TextField(blank=True)

    def __str__(self):
        return f"ArtisanVerification<{self.artisan.user.email}> · {self.status}"

    @property
    def fee_paid(self):
        return self.fee_paid_at is not None

    @property
    def is_editable(self):
        return self.status in (ArtisanVerificationStatus.DRAFT,
                               ArtisanVerificationStatus.AWAITING_PAYMENT,
                               ArtisanVerificationStatus.REJECTED)


class ArtisanDocumentType(models.TextChoices):
    GOVERNMENT_ID = "government_id", "Government ID"
    TRADE_CERTIFICATE = "trade_certificate", "Trade certificate / licence"
    SELFIE = "selfie", "Selfie holding your ID"
    OTHER = "other", "Other supporting document"


class ArtisanVerificationDocument(BaseModel):
    application = models.ForeignKey(ArtisanVerificationApplication, on_delete=models.CASCADE,
                                    related_name="documents")
    doc_type = models.CharField(max_length=30, choices=ArtisanDocumentType.choices)
    file = models.FileField(storage=private_storage, upload_to=artisan_document_upload_to,
                            validators=[validate_document_upload], max_length=255)
    original_name = models.CharField(max_length=255, blank=True)

    @property
    def owner_id_for_storage(self):
        return self.application.artisan.user_id

    def __str__(self):
        return f"{self.get_doc_type_display()} · {self.application.artisan.user.email}"
