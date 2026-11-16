"""Booking lifecycle: pending -> accepted -> in_progress -> completed / cancelled."""
from django.conf import settings
from django.contrib.gis.db import models as gis
from django.db import models

from apps.common.models import BaseModel


class BookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


# Allowed transitions — enforced in the service layer in Phase 2.
BOOKING_TRANSITIONS = {
    BookingStatus.PENDING: {BookingStatus.ACCEPTED, BookingStatus.CANCELLED},
    BookingStatus.ACCEPTED: {BookingStatus.IN_PROGRESS, BookingStatus.CANCELLED},
    BookingStatus.IN_PROGRESS: {BookingStatus.COMPLETED, BookingStatus.CANCELLED},
    BookingStatus.COMPLETED: set(),
    BookingStatus.CANCELLED: set(),
}


class Booking(BaseModel):
    customer = models.ForeignKey(
        "accounts.CustomerProfile", on_delete=models.PROTECT, related_name="bookings"
    )
    artisan = models.ForeignKey(
        "accounts.ArtisanProfile", on_delete=models.PROTECT, related_name="bookings"
    )
    service = models.ForeignKey(
        "catalog.Service", on_delete=models.PROTECT, related_name="bookings"
    )
    status = models.CharField(
        max_length=20, choices=BookingStatus.choices,
        default=BookingStatus.PENDING, db_index=True,
    )
    scheduled_for = models.DateTimeField()
    location = gis.PointField(geography=True)  # where the service happens
    address = models.CharField(max_length=255)
    agreed_price = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["status", "scheduled_for"])]

    def __str__(self):
        return f"Booking<{self.id}> {self.status}"


class BookingStatusHistory(BaseModel):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta(BaseModel.Meta):
        verbose_name_plural = "booking status history"
