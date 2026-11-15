from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class TransferPin(BaseModel):
    """4-digit transaction PIN (hashed) that authorises every outgoing transfer."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="transfer_pin")
    pin_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)


class WalletTransfer(BaseModel):
    """Instant JobCatch wallet → JobCatch wallet transfer."""
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name="wallet_transfers_sent")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="wallet_transfers_received")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=40, unique=True)
    note = models.CharField(max_length=140, blank=True)


class BankTransferStatus(models.TextChoices):
    PROCESSING = "processing", "Processing"
    SUCCESSFUL = "successful", "Successful"
    FAILED = "failed", "Failed · refunded"
    REVERSED = "reversed", "Reversed · refunded"


class BankTransfer(BaseModel):
    """Wallet withdrawal paid out to a Nigerian bank account through Paystack Transfers."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="bank_transfers")
    reference = models.CharField(max_length=50, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bank_code = models.CharField(max_length=20)
    bank_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=10)
    account_name = models.CharField(max_length=150)
    narration = models.CharField(max_length=100, blank=True)
    recipient_code = models.CharField(max_length=60, blank=True)
    transfer_code = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=BankTransferStatus.choices,
                              default=BankTransferStatus.PROCESSING, db_index=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    response = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    @property
    def total(self):
        return self.amount + self.fee
