"""
Identity & access models.

Design (architecture D1): a single custom `User` holds authentication + the
active `role`; each role's domain data lives in a one-to-one Profile. This
avoids multi-table-inheritance joins on every auth check and lets a user hold
multiple profiles later with no migration.
"""
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.gis.db import models as gis
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.common.validators import (
    validate_document_upload,
    validate_image_upload,
    validate_video_upload,
)

from .managers import UserManager


class UserRole(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    ARTISAN = "artisan", "Artisan"
    EMPLOYER = "employer", "Employer"
    JOB_SEEKER = "job_seeker", "Job Seeker"
    ADMIN = "admin", "Admin"


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    email = models.EmailField(unique=True, db_index=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=UserRole.choices, db_index=True)

    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)
    is_identity_verified = models.BooleanField(default=False)  # cached from Verification

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin access

    # Bumping this invalidates every outstanding JWT for the user
    # (powers "logout all devices" and forced logout on password reset).
    token_version = models.PositiveIntegerField(default=0)

    last_login = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self):
        return f"{self.email} ({self.role})"

    def bump_token_version(self):
        """Revoke all outstanding tokens for this user."""
        self.token_version = models.F("token_version") + 1
        self.save(update_fields=["token_version", "updated_at"])
        self.refresh_from_db(fields=["token_version"])


# ------------------------------------------------------------------ profiles
class CustomerProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer_profile")
    location = gis.PointField(geography=True, null=True, blank=True)  # last known
    address = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Customer<{self.user.email}>"


class ArtisanProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="artisan_profile")
    bio = models.TextField(blank=True)
    base_location = gis.PointField(geography=True, null=True, blank=True)
    # Artisans opt in to publishing their phone number for direct calls.
    show_phone = models.BooleanField(default=True)
    service_radius_km = models.PositiveIntegerField(default=25)
    is_available = models.BooleanField(default=True)
    city = models.CharField(max_length=100, blank=True, default="")
    area = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=100, blank=True, default="")

    # Denormalized for fast search/ranking (recomputed via signals/Celery).
    avg_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)
    reputation_score = models.PositiveIntegerField(default=0)
    is_featured = models.BooleanField(default=False)  # driven by subscription tier

    # Set by admin once submitted job-sample videos are approved (see ArtisanJobVideo).
    is_work_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"Artisan<{self.user.email}>"


class ArtisanPortfolioItem(BaseModel):
    artisan = models.ForeignKey(ArtisanProfile, on_delete=models.CASCADE, related_name="portfolio")
    image = models.FileField(upload_to="portfolio/", validators=[validate_image_upload])
    caption = models.CharField(max_length=255, blank=True)


class JobVideoStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ArtisanJobVideo(BaseModel):
    """
    MP4 evidence of previous jobs, submitted as part of an artisan's verification.
    Admin approval of these flips ArtisanProfile.is_work_verified (badge).
    """

    artisan = models.ForeignKey(
        ArtisanProfile, on_delete=models.CASCADE, related_name="job_videos"
    )
    video = models.FileField(upload_to="job_videos/", validators=[validate_video_upload])
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=JobVideoStatus.choices,
        default=JobVideoStatus.PENDING, db_index=True,
    )
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    review_note = models.TextField(blank=True)

    def __str__(self):
        return f"JobVideo<{self.title}> · {self.artisan.user.email}"


class EmployerProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employer_profile")
    company_name = models.CharField(max_length=200)
    cac_number = models.CharField(max_length=50, blank=True)
    is_cac_verified = models.BooleanField(default=False)
    website = models.URLField(blank=True)

    def __str__(self):
        return f"Employer<{self.company_name}>"


class NYSCStatus(models.TextChoices):
    COMPLETED = "completed", "Completed (has certificate)"
    SERVING = "serving", "Currently serving"
    EXEMPTED = "exempted", "Exempted"
    NOT_APPLICABLE = "not_applicable", "Not applicable"


class JobSeekerProfile(BaseModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="job_seeker_profile")
    headline = models.CharField(max_length=200, blank=True)
    skills = models.JSONField(default=list, blank=True)
    active_cv = models.ForeignKey(
        "jobs.CV", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # Graduate job seekers upload their NYSC certificate in addition to a CV.
    is_graduate = models.BooleanField(default=False)
    nysc_status = models.CharField(
        max_length=20, choices=NYSCStatus.choices, default=NYSCStatus.NOT_APPLICABLE
    )
    nysc_certificate = models.FileField(
        upload_to="nysc_certs/", null=True, blank=True, validators=[validate_document_upload]
    )

    def __str__(self):
        return f"JobSeeker<{self.user.email}>"


# ------------------------------------------------------------------ sessions
class SessionRecord(BaseModel):
    """
    One row per active login. Powers device/session management and login
    activity monitoring. `refresh_jti` links to the SimpleJWT refresh token so
    a specific device can be revoked.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    refresh_jti = models.CharField(max_length=64, db_index=True)
    device = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "refresh_jti"])]


# ------------------------------------------------------------------ OTP
class OTPPurpose(models.TextChoices):
    EMAIL_VERIFY = "email_verify", "Email verification"
    PASSWORD_RESET = "password_reset", "Password reset"


class OTP(BaseModel):
    """
    One-time codes for email verification and password reset. The plaintext
    code is never stored — only a salted hash — and is emailed once.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otps")
    purpose = models.CharField(max_length=20, choices=OTPPurpose.choices)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "purpose", "is_used"])]

    @classmethod
    def issue(cls, user, purpose, length, ttl_minutes):
        """Create an OTP, returning (instance, plaintext_code)."""
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        otp = cls.objects.create(
            user=user,
            purpose=purpose,
            code_hash=make_password(code),
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )
        return otp, code

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def verify(self, code):
        """Check a submitted code; consumes the OTP on success."""
        if self.is_used or self.is_expired or self.attempts >= 5:
            return False
        self.attempts = models.F("attempts") + 1
        self.save(update_fields=["attempts"])
        if check_password(code, self.code_hash):
            self.is_used = True
            self.save(update_fields=["is_used"])
            return True
        return False
