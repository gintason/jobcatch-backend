"""Shared model primitives."""
import uuid

from django.conf import settings
from django.db import models


class BaseModel(models.Model):
    """UUID primary key + created/updated timestamps for every entity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("-created_at",)


class AuditLog(BaseModel):
    """Append-only trail for sensitive actions (RBAC changes, status moves, payments)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=100, db_index=True)   # e.g. "booking.status_changed"
    entity = models.CharField(max_length=100)                  # e.g. "bookings.Booking"
    entity_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = "audit log"

    def __str__(self):
        return f"{self.action} · {self.entity}({self.entity_id})"
