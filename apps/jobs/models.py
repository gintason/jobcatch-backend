"""Job portal: postings, CVs, and applications."""
from django.contrib.gis.db import models as gis
from django.db import models

from apps.common.models import BaseModel
from apps.common.validators import validate_upload


class Job(BaseModel):
    employer = models.ForeignKey(
        "accounts.EmployerProfile", on_delete=models.CASCADE, related_name="jobs"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True, on_delete=models.SET_NULL
    )
    location = gis.PointField(geography=True, null=True, blank=True)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_open = models.BooleanField(default=True, db_index=True)

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
