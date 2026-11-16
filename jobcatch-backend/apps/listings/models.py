"""
Paid job listings.

`JobPackage`      admin-configured tiers (Single Post, Featured Post, Bulk packs)
`PackagePurchase` one employer's purchase of a package: a pot of listing
                  credits. Publishing a job spends one credit and keeps the job
                  live for the package's `listing_days`, after which it expires.

Package terms are snapshotted onto the purchase, so an admin editing a package
later never changes what an employer already paid for.
"""
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.common.models import BaseModel


class JobPackage(BaseModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2,
                                help_text="Naira. 0 makes the package free (no checkout).")
    job_credits = models.PositiveIntegerField(default=1, help_text="Jobs that can be published.")
    listing_days = models.PositiveIntegerField(default=30,
                                               help_text="How long each published job stays live.")
    validity_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Unused credits expire this many days after purchase. Blank = never.",
    )
    is_featured = models.BooleanField(
        default=False, help_text="Jobs are pinned above standard listings and on the home page.",
    )
    limit_per_employer = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max purchases per employer (e.g. 1 for a free trial).",
    )
    features = models.JSONField(default=list, blank=True,
                                help_text='Bullet points shown on the pricing card, e.g. ["30 days live"].')
    highlight_label = models.CharField(max_length=40, blank=True, help_text='e.g. "Best value"')
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ("sort_order", "price")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (₦{self.price:,.0f})"


class PurchaseStatus(models.TextChoices):
    PENDING = "pending", "Awaiting payment"
    ACTIVE = "active", "Active"
    EXHAUSTED = "exhausted", "All credits used"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class PackagePurchase(BaseModel):
    employer = models.ForeignKey(
        "accounts.EmployerProfile", on_delete=models.CASCADE, related_name="package_purchases"
    )
    package = models.ForeignKey(JobPackage, on_delete=models.PROTECT, related_name="purchases")
    payment = models.OneToOneField(
        "payments.Payment", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="package_purchase",
    )
    status = models.CharField(max_length=20, choices=PurchaseStatus.choices,
                              default=PurchaseStatus.PENDING, db_index=True)
    # --- snapshot of the package terms at purchase time ---
    price = models.DecimalField(max_digits=12, decimal_places=2)
    credits_total = models.PositiveIntegerField()
    credits_used = models.PositiveIntegerField(default=0)
    listing_days = models.PositiveIntegerField()
    is_featured = models.BooleanField(default=False)
    validity_days = models.PositiveIntegerField(null=True, blank=True)

    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["employer", "status"])]

    def __str__(self):
        return f"{self.package.name} · {self.employer.company_name} · {self.status}"

    @property
    def credits_remaining(self):
        return max(self.credits_total - self.credits_used, 0)

    @property
    def is_usable(self):
        return (self.status == PurchaseStatus.ACTIVE and self.credits_remaining > 0
                and (self.expires_at is None or self.expires_at > timezone.now()))
