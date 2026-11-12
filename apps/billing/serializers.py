from rest_framework import serializers

from .models import BillOrder, Wallet, WalletTransaction


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

    class Meta:
        model = BillOrder
        fields = ("id", "receipt_number", "service", "service_label", "provider", "customer_id",
                  "customer_name", "variation_id", "variation_label", "subscription_type",
                  "amount", "fee", "total", "payment_method", "payment_reference", "status",
                  "status_label", "request_id", "provider_order_id", "provider_message",
                  "token", "units", "quantity", "epins", "epin_packs", "created_at",
                  "completed_at", "refunded_at")

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
        choices=["airtime", "data", "electricity", "tv", "epins", "betting"])
    provider = serializers.CharField(max_length=40)
    # Not used for ePINs (they aren't sent to a phone or account).
    customer_id = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                        default="")
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=5)
    variation_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    subscription_type = serializers.ChoiceField(choices=["change", "renew"], required=False)
    customer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=["wallet", "card"])


class VerifyCustomerSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=["electricity", "tv", "betting"])
    provider = serializers.CharField(max_length=40)
    customer_id = serializers.CharField(max_length=40)
    meter_type = serializers.ChoiceField(choices=["prepaid", "postpaid"], required=False)


class TopupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
