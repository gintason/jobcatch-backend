from rest_framework import serializers

from .models import BillOrder, GiftCardTrade, Wallet, WalletTransaction

# Details the customer may see (never our cost price or VTU.ng's rate).
PUBLIC_DETAILS = ("channel", "account_type", "rmb_amount", "customer_rate", "recipient_name",
                  "recipient_identifier", "recipient_bank_name", "recipient_bank_account_number",
                  "description", "card_amount", "card_currency", "product_name")


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ("balance", "updated_at")


class WalletTransactionSerializer(serializers.ModelSerializer):
    source_label = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = WalletTransaction
        fields = ("id", "kind", "source", "source_label", "amount", "balance_after",
                  "reference", "description", "bill_order", "created_at")


class BillOrderSerializer(serializers.ModelSerializer):
    service_label = serializers.CharField(source="get_service_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    receipt_number = serializers.CharField(read_only=True)
    payment_reference = serializers.CharField(source="payment.reference", read_only=True,
                                              default=None)
    epins = serializers.SerializerMethodField()
    epin_packs = serializers.SerializerMethodField()
    details = serializers.SerializerMethodField()

    class Meta:
        model = BillOrder
        fields = ("id", "receipt_number", "service", "service_label", "provider", "customer_id",
                  "customer_name", "variation_id", "variation_label", "subscription_type",
                  "amount", "fee", "total", "payment_method", "payment_reference", "status",
                  "status_label", "request_id", "provider_order_id", "provider_message",
                  "token", "units", "quantity", "epins", "epin_packs", "service_type", "details",
                  "created_at", "completed_at", "refunded_at")

    def get_details(self, obj):
        return {k: v for k, v in (obj.details or {}).items() if k in PUBLIC_DETAILS}

    def get_epins(self, obj):
        """Text PINs (orders from the old VTU.ng API only)."""
        return [e for e in (obj.epins or []) if isinstance(e, dict) and e.get("pin")]

    def get_epin_packs(self, obj):
        """Voucher packs (current API): each is a printable image of 10 cards."""
        packs = [p for p in (obj.epins or []) if isinstance(p, dict) and p.get("file")]
        return [{"index": i, "network": p.get("network"), "denomination": p.get("denomination"),
                 "pieces_count": p.get("pieces_count"),
                 "image_path": f"/billing/orders/{obj.id}/epin/{i}/"}
                for i, p in enumerate(packs)]


class BillOrderCreateSerializer(serializers.Serializer):
    service = serializers.ChoiceField(
        choices=["airtime", "data", "electricity", "tv", "epins", "betting", "education",
                 "giftcard", "rmb"])
    provider = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    # Not used for ePINs (they aren't sent to a phone or account).
    customer_id = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                        default="")
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=10)
    service_type = serializers.CharField(max_length=30, required=False, allow_blank=True)
    # gift cards
    product_id = serializers.IntegerField(required=False)
    product_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    card_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    card_currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    # RMB
    channel = serializers.CharField(max_length=10, required=False, allow_blank=True)
    account_type = serializers.CharField(max_length=10, required=False, allow_blank=True)
    rmb_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    recipient_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    recipient_identifier = serializers.CharField(max_length=100, required=False, allow_blank=True)
    recipient_bank_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    recipient_bank_account_number = serializers.CharField(max_length=40, required=False,
                                                          allow_blank=True)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    qr_code = serializers.FileField(required=False)

    def validate_qr_code(self, f):
        if f.content_type not in ("image/jpeg", "image/png") or f.size > 2 * 1024 * 1024:
            raise serializers.ValidationError("Upload a JPEG or PNG QR code, up to 2 MB.")
        return f
    variation_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    subscription_type = serializers.ChoiceField(choices=["change", "renew"], required=False)
    customer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=["wallet", "card"])


class VerifyCustomerSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=["electricity", "tv", "betting", "education"])
    variation_id = serializers.CharField(max_length=60, required=False, allow_blank=True)
    provider = serializers.CharField(max_length=40)
    customer_id = serializers.CharField(max_length=40)
    meter_type = serializers.ChoiceField(choices=["prepaid", "postpaid"], required=False)


class TopupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class GiftCardTradeSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # What the customer will receive (VTU.ng's quote less JobCatch's margin).
    expected_payout = serializers.SerializerMethodField()

    def get_expected_payout(self, obj):
        from .services import _trade_payout

        return str(_trade_payout(obj.expected_payout)) if obj.expected_payout is not None else None

    class Meta:
        model = GiftCardTrade
        fields = ("id", "request_id", "slug", "card_name", "card_country", "card_type", "sub_type",
                  "card_currency", "card_amount", "expected_payout", "user_payout", "status",
                  "status_label", "rejection_reason", "created_at", "completed_at")


class GiftCardTradeCreateSerializer(serializers.Serializer):
    slug = serializers.CharField(max_length=60)
    card_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    card_country = serializers.CharField(max_length=4)
    specific_country = serializers.CharField(max_length=60, required=False, allow_blank=True)
    card_type = serializers.ChoiceField(choices=["ecode", "physical"])
    sub_type = serializers.CharField(max_length=40, required=False, allow_blank=True)
    card_currency = serializers.CharField(max_length=3)
    card_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    code = serializers.CharField(max_length=200, required=False, allow_blank=True)
