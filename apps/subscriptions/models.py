"""Artisan/user subscription tiers driving featured listings and visibility boosts."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Plan(models.TextChoices):
    FREE = "free", "Free"
    PREMIUM = "premium", "Premium"
    PRO = "pro", "Pro"


class Subscription(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.FREE)
    is_active = models.BooleanField(default=True, db_index=True)
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.user.email} · {self.plan}"
