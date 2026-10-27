"""
Payment records — the platform's transaction engine.

Every paid feature (bookings, subscriptions, job listing packages, artisan
verification, job seeker Pro, wallet top-ups, bill payments) goes through one
`Payment` row. Each row carries its own invoice number and an append-only
`PaymentEvent` trail (initialised, webhook, verification, fulfilment), which
together form the transaction log, receipts and admin audit trail.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


class Gateway(models.TextChoices):
    PAYSTACK = "paystack", "Paystack"
    FLUTTERWAVE = "flutterwave", "Flutterwave"
    WALLET = "wallet", "JobCatch wallet"


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class PaymentPurpose(models.TextChoices):
    BOOKING = "booking", "Booking"
    SUBSCRIPTION = "subscription", "Subscription"
    JOB_POSTING = "job_posting", "Job listing package"
    VERIFICATION = "verification", "Artisan verification fee"
    CV_SERVICE = "cv_service", "CV Service"
    SEEKER_PRO = "seeker_pro", "Job seeker Pro plan"
    WALLET_TOPUP = "wallet_topup", "Wallet top-up"
    BILL_PAYMENT = "bill_payment", "Bill payment"


def generate_invoice_number():
    """e.g. JCI-20261026-7F3A9C — date-prefixed so invoices sort and read naturally."""
    return f"JCI-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}"


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
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED, db_index=True,
    )
    raw_response = models.JSONField(default=dict, blank=True)  # gateway webhook payload

    # --- invoicing / receipts ---
    invoice_number = models.CharField(
        max_length=40, unique=True, null=True, blank=True, default=generate_invoice_number,
    )
    description = models.CharField(max_length=255, blank=True)
    # Purpose-specific links (package id, job id, bill order id, ...).
    metadata = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Set once the purchased thing has been delivered; guards double fulfilment.
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.reference} · {self.status}"


class PaymentEventKind(models.TextChoices):
    INITIALIZED = "initialized", "Checkout initialised"
    WEBHOOK = "webhook", "Webhook received"
    VERIFIED = "verified", "Verified with gateway"
    SUCCEEDED = "succeeded", "Marked successful"
    FULFILLED = "fulfilled", "Value delivered"
    FAILED = "failed", "Failed"
    ERROR = "error", "Error"
    REFUNDED = "refunded", "Refunded"


class PaymentEvent(BaseModel):
    """Append-only transaction log entry for a payment."""

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=PaymentEventKind.choices)
    source = models.CharField(max_length=40, blank=True)  # api / webhook:paystack / admin ...
    note = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.payment.reference} · {self.kind}"
