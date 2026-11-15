"""In-app notifications (email fan-out handled by Celery tasks in Phase 2)."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Notification(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=40)  # booking_alert, payment_alert, application_alert...
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)  # deep-link payload for clients
    is_read = models.BooleanField(default=False, db_index=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return f"{self.kind} -> {self.user.email}"
