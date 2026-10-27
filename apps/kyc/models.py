"""
Employer KYC (Know Your Customer).

One `EmployerKYC` per employer profile tracks:
  - contact verification   (email + phone, each via OTP)
  - corporate identity     (CAC registration, TIN, director NIN) with the
                           results of automated provider lookups in `checks`
  - documents              (`KYCDocument`, stored in private storage)
  - review status          not_started -> in_progress -> pending -> verified | rejected

Every state change is written to the common AuditLog, which is what the admin
review screen shows as the case history.
"""
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.common.storage import kyc_document_upload_to, private_storage
from apps.common.validators import validate_document_upload


class KYCStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    IN_PROGRESS = "in_progress", "In progress"
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class CompanyType(models.TextChoices):
    RC = "RC", "Limited company (RC)"
    BN = "BN", "Business name (BN)"
    IT = "IT", "Incorporated trustees (IT)"


class CheckStatus(models.TextChoices):
    PASSED = "passed", "Passed"
    FAILED = "failed", "Failed"
    ERROR = "error", "Lookup error"
    SKIPPED = "skipped", "Not checked (manual review)"


# The automated lookups, in the order they run.
CHECK_KEYS = ("cac", "tin", "nin")


class EmployerKYC(BaseModel):
    employer = models.OneToOneField(
        "accounts.EmployerProfile", on_delete=models.CASCADE, related_name="kyc"
    )
    status = models.CharField(
        max_length=20, choices=KYCStatus.choices, default=KYCStatus.NOT_STARTED, db_index=True
    )

    # --- corporate identity ---
    registered_name = models.CharField(max_length=200, blank=True)
    company_type = models.CharField(max_length=2, choices=CompanyType.choices, default=CompanyType.RC)
    cac_number = models.CharField(max_length=20, blank=True)       # digits only, e.g. 1234567
    tin = models.CharField(max_length=20, blank=True)
    director_name = models.CharField(max_length=150, blank=True)
    director_nin = models.CharField(max_length=11, blank=True)     # never returned unmasked to the owner
    business_address = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=60, blank=True)

    # --- contact verification ---
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    # The exact value that was verified: editing the field afterwards silently
    # un-verifies it, because the stored value no longer matches.
    email_verified_value = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_value = models.CharField(max_length=20, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    # --- automated checks: {"cac": {"status", "message", "checked_at", "data"}, ...}
    checks = models.JSONField(default=dict, blank=True)

    # --- review ---
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="kyc_reviews",
    )
    rejection_reason = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    # True when an admin decided against (or without) the automated results.
    is_manual_override = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name = "employer KYC"
        verbose_name_plural = "employer KYC cases"

    def __str__(self):
        return f"KYC<{self.employer.company_name}> · {self.status}"

    @property
    def user(self):
        return self.employer.user

    @property
    def is_email_verified(self):
        return bool(self.contact_email) and \
            self.contact_email.lower() == (self.email_verified_value or "").lower()

    @property
    def is_phone_verified(self):
        from apps.common.phone import InvalidPhone, to_local

        if not self.contact_phone or not self.phone_verified_value:
            return False
        try:
            return to_local(self.contact_phone) == to_local(self.phone_verified_value)
        except InvalidPhone:
            return False

    @property
    def is_editable(self):
        return self.status in (KYCStatus.NOT_STARTED, KYCStatus.IN_PROGRESS, KYCStatus.REJECTED)

    def check_status(self, key):
        return (self.checks or {}).get(key, {}).get("status")

    @property
    def all_checks_passed(self):
        return all(self.check_status(k) == CheckStatus.PASSED for k in CHECK_KEYS)


class KYCDocumentType(models.TextChoices):
    CAC_CERTIFICATE = "cac_certificate", "CAC certificate"
    UTILITY_BILL = "utility_bill", "Utility bill (proof of address)"
    GOVERNMENT_ID = "government_id", "Director's government ID"
    TIN_CERTIFICATE = "tin_certificate", "TIN certificate"
    OTHER = "other", "Other supporting document"


REQUIRED_DOCUMENTS = (
    KYCDocumentType.CAC_CERTIFICATE,
    KYCDocumentType.UTILITY_BILL,
    KYCDocumentType.GOVERNMENT_ID,
)


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"


class KYCDocument(BaseModel):
    kyc = models.ForeignKey(EmployerKYC, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=KYCDocumentType.choices)
    file = models.FileField(
        storage=private_storage, upload_to=kyc_document_upload_to,
        validators=[validate_document_upload], max_length=255,
    )
    original_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING
    )
    review_note = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_doc_type_display()} · {self.kyc.employer.company_name}"

    @property
    def owner_id_for_storage(self):
        return self.kyc.employer.user_id


class OTPChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class ContactOTP(BaseModel):
    """
    One-time code proving control of a specific email address or phone number.
    Bound to the destination, so a code sent to one number can't verify another.
    Only a hash of the code is stored.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contact_otps"
    )
    channel = models.CharField(max_length=10, choices=OTPChannel.choices)
    destination = models.CharField(max_length=254)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)

    MAX_ATTEMPTS = 5

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "channel", "is_used"])]

    @classmethod
    def issue(cls, user, channel, destination, length=6):
        import secrets

        code = "".join(secrets.choice("0123456789") for _ in range(length))
        # A new code supersedes any unused one for the same channel.
        cls.objects.filter(user=user, channel=channel, is_used=False).update(is_used=True)
        otp = cls.objects.create(
            user=user, channel=channel, destination=destination,
            code_hash=make_password(code),
            expires_at=timezone.now() + timezone.timedelta(minutes=settings.KYC_OTP_TTL_MINUTES),
        )
        return otp, code

    def verify(self, code):
        if self.is_used or timezone.now() >= self.expires_at or self.attempts >= self.MAX_ATTEMPTS:
            return False
        self.attempts += 1
        if check_password(code, self.code_hash):
            self.is_used = True
        self.save(update_fields=["attempts", "is_used", "updated_at"])
        return self.is_used
