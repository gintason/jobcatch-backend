"""Service taxonomy + listings."""
from django.db import models
from django.utils.text import slugify

from apps.common.models import BaseModel


class Category(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta(BaseModel.Meta):
        verbose_name_plural = "categories"

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
