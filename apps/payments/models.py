"""Payment records for bookings, subscriptions, job posts, and verification fees."""
from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Gateway(models.TextChoices):
    PAYSTACK = "paystack", "Paystack"
    FLUTTERWAVE = "flutterwave", "Flutterwave"


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class PaymentPurpose(models.TextChoices):
    BOOKING = "booking", "Booking"
    SUBSCRIPTION = "subscription", "Subscription"
    JOB_POSTING = "job_posting", "Job posting"
    VERIFICATION = "verification", "Verification"


class Payment(BaseModel):
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    purpose = models.CharField(max_length=20, choices=PaymentPurpose.choices)
    booking = models.ForeignKey(
        "bookings.Booking", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="payments",
    )
    gateway = models.CharField(max_length=20, choices=Gateway.choices)
    reference = models.CharField(max_length=100, unique=True, db_index=True)  # gateway ref
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED, db_index=True,
    )
    raw_response = models.JSONField(default=dict, blank=True)  # gateway webhook payload

    def __str__(self):
        return f"{self.reference} · {self.status}"
