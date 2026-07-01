"""Identity / document verification workflow feeding the 'verified badge'."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.common.validators import validate_upload


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
