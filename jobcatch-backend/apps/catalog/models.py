"""Service taxonomy + listings."""
from django.db import models
from django.utils.text import slugify

from apps.common.models import BaseModel


class CategoryKind(models.TextChoices):
    """
    The two marketplaces have entirely separate taxonomies: a customer booking a
    plumber and an employer hiring a petroleum engineer share no vocabulary.
    One model, two disjoint sets, filtered by `kind`.
    """

    HOME_SERVICE = "home_service", "Home service"
    JOB = "job", "Job listing"


class Category(BaseModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(blank=True)
    kind = models.CharField(
        max_length=20, choices=CategoryKind.choices,
        default=CategoryKind.HOME_SERVICE, db_index=True,
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta(BaseModel.Meta):
        verbose_name_plural = "categories"
        # Names are unique *within* a taxonomy, not across it: "Photographer"
        # can legitimately exist as both a home service and a job category.
        constraints = [
            models.UniqueConstraint(fields=["kind", "name"], name="uniq_category_kind_name"),
            models.UniqueConstraint(fields=["kind", "slug"], name="uniq_category_kind_slug"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Service(BaseModel):
    artisan = models.ForeignKey(
        "accounts.ArtisanProfile", on_delete=models.CASCADE, related_name="services"
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="services")
    title = models.CharField(max_length=200)
    description = models.TextField()
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["category", "is_active"])]

    def __str__(self):
        return f"{self.title} · {self.artisan.user.email}"
