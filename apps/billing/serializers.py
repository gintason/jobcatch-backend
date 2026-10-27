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

    class Meta:
        model = BillOrder
        fields = ("id", "receipt_number", "service", "service_label", "provider", "customer_id",
                  "customer_name", "variation_id", "variation_label", "subscription_type",
                  "amount", "fee", "total", "payment_method", "payment_reference", "status",
                  "status_label", "request_id", "provider_order_id", "provider_message",
                  "token", "units", "quantity", "epins", "created_at", "completed_at",
                  "refunded_at")


class BillOrderCreateSerializer(serializers.Serializer):
    service = serializers.ChoiceField(
        choices=["airtime", "data", "electricity", "tv", "epins", "betting"])
    provider = serializers.CharField(max_length=40)
    # Not used for ePINs (they aren't sent to a phone or account).
    customer_id = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                        default="")
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=40)
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
