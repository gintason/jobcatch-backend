from rest_framework import serializers

from apps.bookings.models import Booking

from .models import Payment, PaymentPurpose


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
        return data


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ("id", "purpose", "booking", "gateway", "reference",
                  "amount", "commission", "status", "created_at")
        read_only_fields = fields
