"""
Wallet + bill payments (airtime, data, electricity, cable TV, recharge PINs,
betting wallet funding) via VTU.ng.

Wallet           one per user; balance only ever changes through a
                 WalletTransaction row (double-entry style ledger), inside a
                 row lock, so the balance always equals the ledger.
BillOrder        one bill purchase. Paid from the wallet or by card/bank
                 (Payment, purpose=bill_payment). If the biller fails or refunds,
                 the full amount is credited back to the user's wallet.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.common.storage import private_storage


def bill_attachment_upload_to(instance, filename):
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"bill-attachments/{instance.user_id}/{uuid.uuid4().hex}{ext}"


class Wallet(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="wallet")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    class Meta(BaseModel.Meta):
        constraints = [
            models.CheckConstraint(check=models.Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"Wallet<{self.user.email}> ₦{self.balance}"


class TxnKind(models.TextChoices):
    CREDIT = "credit", "Credit"
    DEBIT = "debit", "Debit"


class TxnSource(models.TextChoices):
    TOPUP = "topup", "Wallet top-up"
    BILL = "bill", "Bill payment"
    REFUND = "refund", "Refund"
    ADJUSTMENT = "adjustment", "Admin adjustment"
    GIFTCARD_SALE = "giftcard_sale", "Gift card sale"


class WalletTransaction(BaseModel):
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=TxnKind.choices)
    source = models.CharField(max_length=20, choices=TxnSource.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    # Unique per business event (e.g. "topup:JC-...", "refund:<order>") so a
    # retried webhook can never credit twice.
    reference = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    payment = models.ForeignKey("payments.Payment", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    bill_order = models.ForeignKey("BillOrder", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="wallet_transactions")

    def __str__(self):
        return f"{self.kind} ₦{self.amount} · {self.reference}"


class BillService(models.TextChoices):
    AIRTIME = "airtime", "Airtime"
    DATA = "data", "Data bundle"
    ELECTRICITY = "electricity", "Electricity"
    TV = "tv", "Cable TV"
    EPINS = "epins", "Recharge card PINs"
    BETTING = "betting", "Betting wallet funding"
    EDUCATION = "education", "Exam PINs (WAEC / JAMB)"
    GIFTCARD = "giftcard", "Gift card"
    RMB = "rmb", "RMB payment (China)"


class PaymentMethod(models.TextChoices):
    WALLET = "wallet", "Wallet"
    CARD = "card", "Card / bank transfer"


class BillStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
    QUEUED = "queued", "Paid, sending to biller"
    PROCESSING = "processing", "Processing"
    SUCCESSFUL = "successful", "Successful"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded to wallet"
    CANCELLED = "cancelled", "Cancelled"


class BillOrder(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="bill_orders")
    service = models.CharField(max_length=20, choices=BillService.choices, db_index=True)
    provider = models.CharField(max_length=40)              # VTU service_id: mtn, ikeja-electric, dstv, Bet9ja
    customer_id = models.CharField(max_length=40, blank=True)  # phone / meter / smartcard / betting ID
    variation_id = models.CharField(max_length=40, blank=True)   # data plan / meter type / bouquet
    variation_label = models.CharField(max_length=120, blank=True)
    subscription_type = models.CharField(max_length=10, blank=True)  # tv: change | renew
    customer_name = models.CharField(max_length=150, blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)    # face value sent to the biller
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2)     # what the user pays

    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    payment = models.ForeignKey("payments.Payment", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="bill_orders")
    status = models.CharField(max_length=20, choices=BillStatus.choices,
                              default=BillStatus.AWAITING_PAYMENT, db_index=True)

    # --- biller side ---
    request_id = models.CharField(max_length=50, unique=True)
    provider_order_id = models.CharField(max_length=40, blank=True)
    provider_status = models.CharField(max_length=40, blank=True)
    provider_message = models.CharField(max_length=255, blank=True)
    token = models.CharField(max_length=120, blank=True)        # electricity token
    units = models.CharField(max_length=40, blank=True)
    # ePINs: how many cards were bought, and the PINs once the biller delivers
    # them ([{"pin", "serial", "amount", "instruction"}]). Only ever shown to
    # the buyer (orders are scoped to their owner).
    quantity = models.PositiveSmallIntegerField(default=1)
    epins = models.JSONField(default=list, blank=True)
    # education: result_checker | registration | jamb
    service_type = models.CharField(max_length=30, blank=True)
    # Service-specific inputs and quotes, e.g. RMB recipient + rate, gift card
    # product + VTU.ng's NGN price.
    details = models.JSONField(default=dict, blank=True)
    # RMB: the recipient's Alipay / WeChat receive QR code (private).
    attachment = models.FileField(storage=private_storage, upload_to=bill_attachment_upload_to,
                                  null=True, blank=True, max_length=255)
    response = models.JSONField(default=dict, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.get_service_display()} {self.provider} {self.customer_id} · {self.status}"

    @property
    def receipt_number(self):
        return f"JCB-{self.created_at:%Y%m%d}-{str(self.id)[:8].upper()}"


class GiftCardTradeStatus(models.TextChoices):
    SUBMITTING = "submitting", "Submitting"
    PENDING = "pending", "Under review"
    COMPLETED = "completed", "Paid to wallet"
    CANCELLED = "cancelled", "Declined"
    FAILED = "failed", "Not submitted"


def giftcard_image_upload_to(instance, filename):
    import os
    import uuid

    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"giftcard-trades/{instance.user_id}/{uuid.uuid4().hex}{ext}"


class GiftCardTrade(BaseModel):
    """
    A customer selling a gift card. VTU.ng reviews it; when they approve, the
    payout (less JobCatch's margin) is credited to the customer's JobCatch wallet.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="giftcard_trades")
    request_id = models.CharField(max_length=50, unique=True)
    slug = models.CharField(max_length=60)
    card_name = models.CharField(max_length=120, blank=True)
    card_country = models.CharField(max_length=4)
    specific_country = models.CharField(max_length=60, blank=True)
    card_type = models.CharField(max_length=10)               # ecode | physical
    sub_type = models.CharField(max_length=40, blank=True)
    card_currency = models.CharField(max_length=3)
    card_amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The redemption code (e-codes). Shown to nobody after submission.
    code = models.CharField(max_length=200, blank=True)
    images = models.JSONField(default=list, blank=True)       # private storage paths
    expected_payout = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    provider_payout = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    user_payout = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=GiftCardTradeStatus.choices,
                              default=GiftCardTradeStatus.SUBMITTING, db_index=True)
    provider_reference = models.CharField(max_length=40, blank=True, db_index=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    response = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.card_name or self.slug} {self.card_currency} {self.card_amount} · {self.status}"
