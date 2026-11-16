"""Job portal: postings, CVs, and applications."""
from django.contrib.gis.db import models as gis
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import BaseModel
from apps.common.validators import validate_upload


class JobQuerySet(models.QuerySet):
    def live(self):
        """
        Jobs seekers can see and apply to: open, published (paid for when paid
        listings are on), and not past their listing period. Expiry is enforced
        here at query time, so a late cron run never shows a stale listing.
        """
        return self.filter(is_open=True, published_at__isnull=False).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        )


class Job(BaseModel):
    employer = models.ForeignKey(
        "accounts.EmployerProfile", on_delete=models.CASCADE, related_name="jobs"
    )
    title = models.CharField(max_length=200)
    # Stored exactly as typed (newlines/paragraphs preserved); rendered with
    # white-space: pre-wrap on the web.
    description = models.TextField()
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True, on_delete=models.SET_NULL
    )
    location = gis.PointField(geography=True, null=True, blank=True)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_open = models.BooleanField(default=True, db_index=True)

    # --- listing lifecycle (paid job listings) ---
    # null = draft / awaiting payment; set when a listing credit is spent.
    # Defaults to "now" so jobs created by staff (admin, scripts) are live;
    # the employer API creates drafts explicitly when paid listings are on.
    published_at = models.DateTimeField(
        null=True, blank=True, db_index=True, default=timezone.now
    )
    # null = never expires (legacy / free listings).
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    listing_purchase = models.ForeignKey(
        "listings.PackagePurchase", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="jobs",
    )

    objects = JobQuerySet.as_manager()

    @property
    def listing_status(self):
        if not self.published_at:
            return "draft"
        if self.expires_at and self.expires_at <= timezone.now():
            return "expired"
        if not self.is_open:
            return "closed"
        return "active"

    def __str__(self):
        return self.title


class CV(BaseModel):
    seeker = models.ForeignKey(
        "accounts.JobSeekerProfile", on_delete=models.CASCADE, related_name="cvs"
    )
    file = models.FileField(upload_to="cvs/", validators=[validate_upload])  # PDF policy enforced
    title = models.CharField(max_length=150)

    class Meta(BaseModel.Meta):
        verbose_name = "CV"

    def __str__(self):
        return self.title


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    SHORTLISTED = "shortlisted", "Shortlisted"
    REJECTED = "rejected", "Rejected"
    HIRED = "hired", "Hired"


class Application(BaseModel):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="applications")
    seeker = models.ForeignKey(
        "accounts.JobSeekerProfile", on_delete=models.CASCADE, related_name="applications"
    )
    cv = models.ForeignKey(CV, on_delete=models.PROTECT)
    cover_letter = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED, db_index=True,
    )

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["job", "seeker"], name="uniq_application_per_job")
        ]

    def __str__(self):
        return f"{self.seeker.user.email} -> {self.job.title}"
