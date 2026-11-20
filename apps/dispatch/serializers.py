from decimal import Decimal

from rest_framework import serializers

from apps.common.phone import InvalidPhone, to_local

from .models import Delivery, DeliveryKind, DeliveryRate, DeliveryStatus, RiderProfile, VehicleType


def _phone(value):
    try:
        return to_local(value)
    except InvalidPhone as exc:
        raise serializers.ValidationError(str(exc)) from exc


class RateSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="get_vehicle_type_display", read_only=True)

    class Meta:
        model = DeliveryRate
        fields = ("vehicle_type", "label", "base_fare", "per_km", "minimum_fare", "errand_fee")


class QuoteSerializer(serializers.Serializer):
    pickup_lat = serializers.FloatField()
    pickup_lng = serializers.FloatField()
    dropoff_lat = serializers.FloatField()
    dropoff_lng = serializers.FloatField()
    vehicle_type = serializers.ChoiceField(choices=VehicleType.choices)
    kind = serializers.ChoiceField(choices=DeliveryKind.choices, default=DeliveryKind.PACKAGE)


class DeliveryCreateSerializer(QuoteSerializer):
    pickup_address = serializers.CharField(max_length=255)
    pickup_contact_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    pickup_contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    dropoff_address = serializers.CharField(max_length=255)
    recipient_name = serializers.CharField(max_length=100)
    recipient_phone = serializers.CharField(max_length=20)
    item_description = serializers.CharField(max_length=255)
    errand_details = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    # Riders never buy items: this is ignored (kept so older app versions still work).
    errand_budget = serializers.DecimalField(max_digits=12, decimal_places=2, required=False,
                                             allow_null=True, min_value=Decimal("0"))
    # Food & essentials pickup: the customer confirms they've already paid the vendor.
    order_paid = serializers.BooleanField(required=False, default=False)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_recipient_phone(self, v):
        return _phone(v)

    def validate_pickup_contact_phone(self, v):
        return _phone(v) if v else v


class RiderPublicSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.full_name", read_only=True)
    vehicle_label = serializers.CharField(source="get_vehicle_type_display", read_only=True)
    rating = serializers.FloatField(read_only=True)

    class Meta:
        model = RiderProfile
        fields = ("name", "phone", "vehicle_type", "vehicle_label", "plate_number", "rating",
                  "completed_count")


class DeliverySerializer(serializers.ModelSerializer):
    """For the customer and the assigned rider. The delivery code is only shown to the customer."""

    reference = serializers.CharField(read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    vehicle_label = serializers.CharField(source="get_vehicle_type_display", read_only=True)
    pickup = serializers.SerializerMethodField()
    dropoff = serializers.SerializerMethodField()
    rider = RiderPublicSerializer(read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    customer_phone = serializers.SerializerMethodField()
    delivery_code = serializers.SerializerMethodField()
    payment_reference = serializers.CharField(source="payment.reference", read_only=True, default=None)
    distance_to_pickup_km = serializers.SerializerMethodField()

    class Meta:
        model = Delivery
        fields = ("id", "reference", "kind", "kind_label", "vehicle_type", "vehicle_label", "status",
                  "status_label", "pickup_address", "pickup", "pickup_contact_name",
                  "pickup_contact_phone", "dropoff_address", "dropoff", "recipient_name",
                  "recipient_phone", "item_description", "errand_details", "errand_budget", "notes",
                  "distance_km", "fare", "rider_share", "platform_share", "rider", "customer_name",
                  "customer_phone", "delivery_code", "payment_reference", "rating", "review",
                  "distance_to_pickup_km", "created_at", "accepted_at", "picked_up_at", "paid_at",
                  "delivered_at", "cancelled_at", "cancel_reason")

    def _viewer(self):
        req = self.context.get("request")
        return getattr(req, "user", None)

    def get_pickup(self, obj):
        return {"lat": obj.pickup_point.y, "lng": obj.pickup_point.x}

    def get_dropoff(self, obj):
        return {"lat": obj.dropoff_point.y, "lng": obj.dropoff_point.x}

    def get_customer_phone(self, obj):
        # Riders see the customer's phone only once they've accepted the job.
        viewer = self._viewer()
        if viewer and obj.rider_id and obj.rider.user_id == viewer.id \
                and obj.status not in (DeliveryStatus.SEARCHING,):
            return obj.customer.phone
        return None

    def get_delivery_code(self, obj):
        viewer = self._viewer()
        return obj.delivery_code if viewer and viewer.id == obj.customer_id else None

    def get_distance_to_pickup_km(self, obj):
        d = getattr(obj, "distance_to_pickup", None)
        return round(d.km, 1) if d is not None else None


class OpenRequestSerializer(DeliverySerializer):
    """What a rider sees before accepting: no names or phone numbers yet."""

    class Meta(DeliverySerializer.Meta):
        fields = ("id", "reference", "kind", "kind_label", "vehicle_type", "pickup_address",
                  "pickup", "dropoff_address", "dropoff", "item_description", "errand_details",
                  "errand_budget", "distance_km", "fare", "rider_share", "distance_to_pickup_km",
                  "created_at")


class RiderProfileSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    vehicle_label = serializers.CharField(source="get_vehicle_type_display", read_only=True)
    has_id_document = serializers.SerializerMethodField()
    has_selfie = serializers.SerializerMethodField()
    id_type_label = serializers.CharField(source="get_id_type_display", read_only=True)
    missing = serializers.SerializerMethodField()
    account_number_masked = serializers.SerializerMethodField()
    has_payout_account = serializers.SerializerMethodField()
    can_work = serializers.BooleanField(read_only=True)
    rating = serializers.FloatField(read_only=True)
    share_percent = serializers.SerializerMethodField()

    class Meta:
        model = RiderProfile
        fields = ("phone", "vehicle_type", "vehicle_label", "plate_number", "city", "state",
                  "status", "status_label", "review_note", "id_type", "id_type_label", "id_number",
                  "has_id_document", "has_selfie", "missing", "bank_name",
                  "account_name", "account_number_masked", "has_payout_account", "is_online",
                  "can_work", "completed_count", "rating", "share_percent")
        read_only_fields = ("status", "review_note", "bank_name", "account_name", "is_online",
                            "completed_count")

    def get_has_id_document(self, obj):
        return bool(obj.id_document)

    def get_has_selfie(self, obj):
        return bool(obj.selfie)

    def get_missing(self, obj):
        """What the rider still has to provide before their application can be reviewed."""
        checks = [("phone", obj.phone), ("ID type", obj.id_type), ("ID number", obj.id_number),
                  ("ID document", obj.id_document), ("selfie", obj.selfie), ("payout account", obj.subaccount_code)]
        return [label for label, ok in checks if not ok]

    def validate_id_number(self, value):
        value = (value or "").strip().upper()
        if value and not (5 <= len(value) <= 30 and value.replace("-", "").replace(" ", "").isalnum()):
            raise serializers.ValidationError("Enter the number exactly as it appears on your ID.")
        return value

    def get_account_number_masked(self, obj):
        return f"******{obj.account_number[-4:]}" if obj.account_number else ""

    def get_has_payout_account(self, obj):
        return bool(obj.subaccount_code)

    def get_share_percent(self, obj):
        from django.conf import settings

        return str(settings.DISPATCH_RIDER_SHARE_PERCENT)

    def validate_phone(self, v):
        return _phone(v)


class AdminRiderSerializer(RiderProfileSerializer):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    account_number = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    reviewed_at = serializers.DateTimeField(read_only=True)

    class Meta(RiderProfileSerializer.Meta):
        fields = ("id", "full_name", "email", "account_number", "created_at", "reviewed_at",
                  *RiderProfileSerializer.Meta.fields)
