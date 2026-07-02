"""
Booking state-machine service.

Every status change goes through `transition_booking`, which (1) rejects illegal
moves per BOOKING_TRANSITIONS and (2) records a BookingStatusHistory row so we
have a full audit trail of who changed what and when.
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import BOOKING_TRANSITIONS, Booking, BookingStatus, BookingStatusHistory


def transition_booking(booking: Booking, to_status: str, actor) -> Booking:
    allowed = BOOKING_TRANSITIONS.get(booking.status, set())
    if to_status not in allowed:
        raise ValidationError(
            f"Illegal transition: a '{booking.status}' booking cannot become "
            f"'{to_status}'. Allowed: {sorted(s.value for s in allowed) or 'none'}."
        )
    from_status = booking.status
    booking.status = to_status
    booking.save(update_fields=["status", "updated_at"])
    BookingStatusHistory.objects.create(
        booking=booking,
        from_status=from_status,
        to_status=to_status,
        changed_by=actor,
    )
    return booking


def can_review(booking: Booking) -> bool:
    """A review is only allowed on a completed booking with no existing review."""
    return booking.status == BookingStatus.COMPLETED and not hasattr(booking, "review")
