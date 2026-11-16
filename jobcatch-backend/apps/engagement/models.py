"""
Conversion & engagement.

UpgradePrompt        admin-editable in-app pop-ups / banners shown to users
                     who match a trigger (e.g. job seeker without Pro, artisan
                     not yet verified), each with a CTA straight to checkout.
PromptState          per-user frequency capping (seen / dismissed / clicked).
JobAlertSubscription a Pro job seeker's WhatsApp alert preferences.
JobAlertDelivery     one alert sent for one job (idempotency + audit).
"""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Audience(models.TextChoices):
    JOB_SEEKER = "job_seeker", "Job seekers"
    ARTISAN = "artisan", "Artisans"
    EMPLOYER = "employer", "Employers"


class Trigger(models.TextChoices):
    SEEKER_NOT_PRO = "seeker_not_pro", "Job seeker without Pro"
    ARTISAN_NOT_VERIFIED = "artisan_not_verified", "Artisan not verified"
    ARTISAN_FREE_TIER = "artisan_free_tier", "Verified artisan on the free tier"
    EMPLOYER_NOT_VERIFIED = "employer_not_verified", "Employer not KYC-verified"
    EMPLOYER_NO_CREDITS = "employer_no_credits", "Verified employer with no listing credits"


class PromptStyle(models.TextChoices):
    MODAL = "modal", "Pop-up"
    BANNER = "banner", "Banner"


class UpgradePrompt(BaseModel):
    key = models.SlugField(unique=True)
    audience = models.CharField(max_length=20, choices=Audience.choices)
    trigger = models.CharField(max_length=40, choices=Trigger.choices)
    style = models.CharField(max_length=10, choices=PromptStyle.choices, default=PromptStyle.BANNER)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=300)
    highlights = models.JSONField(default=list, blank=True, help_text="Bullet points (pop-ups).")
    cta_label = models.CharField(max_length=40)
    cta_url = models.CharField(max_length=200, help_text="App path, e.g. /checkout/seeker-pro")
    # How long a dismissed prompt stays hidden; also the minimum gap between pop-ups.
    cooldown_hours = models.PositiveIntegerField(default=72)
    priority = models.PositiveIntegerField(default=100, help_text="Lower shows first.")
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ("priority", "key")

    def __str__(self):
        return f"{self.key} ({self.get_style_display()})"


class PromptState(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="prompt_states")
    prompt = models.ForeignKey(UpgradePrompt, on_delete=models.CASCADE, related_name="states")
    shown_count = models.PositiveIntegerField(default=0)
    last_shown_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["user", "prompt"], name="uniq_prompt_state")]


class JobAlertSubscription(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="job_alerts")
    whatsapp_number = models.CharField(max_length=20, blank=True)
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+",
                                        help_text="Empty = every category.")
    keywords = models.CharField(max_length=200, blank=True,
                                help_text="Comma-separated; empty = any title.")
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"JobAlerts<{self.user.email}>"

    @property
    def keyword_list(self):
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]


class DeliveryStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class JobAlertDelivery(BaseModel):
    subscription = models.ForeignKey(JobAlertSubscription, on_delete=models.CASCADE,
                                     related_name="deliveries")
    job = models.ForeignKey("jobs.Job", on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=DeliveryStatus.choices)
    error = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [models.UniqueConstraint(fields=["subscription", "job"],
                                               name="uniq_alert_per_job")]
