"""Chat data model. Realtime consumers (Django Channels) land in Phase 3."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.common.validators import validate_upload


class Conversation(BaseModel):
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="conversations"
    )
    # A conversation is optionally scoped to a booking or a job thread.
    booking = models.ForeignKey(
        "bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL
    )
    job = models.ForeignKey(
        "jobs.Job", null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return f"Conversation<{self.id}>"


class Message(BaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to="chat/", null=True, blank=True, validators=[validate_upload]
    )
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("created_at",)  # chronological within a thread
        indexes = [models.Index(fields=["conversation", "created_at"])]
