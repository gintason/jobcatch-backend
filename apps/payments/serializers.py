from django.conf import settings
from rest_framework import serializers

from apps.bookings.models import Booking

from .gateways import available_gateways
from .models import Payment, PaymentEvent, PaymentPurpose


def validate_gateway(value):
    """Accept a gateway only if it's configured; blank -> the default."""
    if not value:
        return settings.DEFAULT_PAYMENT_GATEWAY
    if value not in ("paystack", "flutterwave"):
        raise serializers.ValidationError("Unsupported payment gateway.")
    configured = available_gateways()
    if configured and value not in configured:
        raise serializers.ValidationError(f"{value.title()} is not available right now.")
    return value


class GatewayField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        return validate_gateway(super().to_internal_value(data))


class PaymentInitializeSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(
        choices=[
            PaymentPurpose.BOOKING,
            PaymentPurpose.SUBSCRIPTION,
            PaymentPurpose.CV_SERVICE,
        ]
    )
    booking = serializers.PrimaryKeyRelatedField(
        queryset=Booking.objects.all(), required=False
    )
    plan = serializers.ChoiceField(choices=["premium", "pro"], required=False)
    gateway = GatewayField()

    def validate(self, data):
        request = self.context["request"]
        if data["purpose"] == PaymentPurpose.BOOKING:
            booking = data.get("booking")
            if not booking:
                raise serializers.ValidationError("A booking id is required.")
            if booking.customer.user != request.user:
                raise serializers.ValidationError("You can only pay for your own booking.")
        elif data["purpose"] == PaymentPurpose.SUBSCRIPTION and not data.get("plan"):
            raise serializers.ValidationError("A subscription plan is required.")
        data.setdefault("gateway", settings.DEFAULT_PAYMENT_GATEWAY)
        return data


class PaymentSerializer(serializers.ModelSerializer):
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)

    class Meta:
        model = Payment
        fields = ("id", "purpose", "purpose_label", "booking", "gateway", "reference",
                  "invoice_number", "description", "amount", "commission", "currency",
                  "status", "paid_at", "fulfilled_at", "failure_reason", "metadata",
                  "created_at")
        read_only_fields = fields


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = ("kind", "source", "note", "created_at")


class ReceiptSerializer(serializers.ModelSerializer):
    """Printable receipt / invoice for one payment."""

    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    gateway_label = serializers.CharField(source="get_gateway_display", read_only=True)
    payer_name = serializers.CharField(source="payer.full_name", read_only=True)
    payer_email = serializers.EmailField(source="payer.email", read_only=True)
    events = PaymentEventSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = ("id", "invoice_number", "reference", "purpose", "purpose_label",
                  "description", "amount", "currency", "status", "gateway", "gateway_label",
                  "payer_name", "payer_email", "paid_at", "created_at", "metadata", "events")
        read_only_fields = fields
