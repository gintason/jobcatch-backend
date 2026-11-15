from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class ReferralCode(BaseModel):
    """A user's personal invite code (created the first time they open Referrals)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="referral_code")
    code = models.CharField(max_length=12, unique=True)

    def __str__(self):
        return self.code


class ReferralStatus(models.TextChoices):
    PENDING = "pending", "Waiting for first purchase"
    REWARDED = "rewarded", "Rewarded"
    REJECTED = "rejected", "Not eligible"


class Referral(BaseModel):
    """One invited user. Rewarded once, after the invitee's first qualifying purchase."""
    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="referrals_made")
    referee = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="referred_by")
    code = models.CharField(max_length=12)
    status = models.CharField(max_length=12, choices=ReferralStatus.choices,
                              default=ReferralStatus.PENDING, db_index=True)
    reward_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    referee_bonus = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    qualifying_reference = models.CharField(max_length=60, blank=True)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["referrer", "status"])]

    def __str__(self):
        return f"{self.referrer_id} → {self.referee_id} ({self.status})"
