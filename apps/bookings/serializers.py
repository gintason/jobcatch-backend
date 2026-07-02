"""Booking serializers."""
from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import Booking, BookingStatusHistory


class BookingCreateSerializer(serializers.ModelSerializer):
    """Customer books a service. Artisan + price are derived server-side."""

    latitude = serializers.FloatField(write_only=True)
    longitude = serializers.FloatField(write_only=True)

    class Meta:
        model = Booking
        fields = ("service", "scheduled_for", "latitude", "longitude", "address", "notes")

    def validate_service(self, service):
        if not service.is_active:
            raise serializers.ValidationError("This service is not currently available.")
        return service

    def create(self, validated):
        request = self.context["request"]
        service = validated["service"]
        lat = validated.pop("latitude")
        lng = validated.pop("longitude")
        return Booking.objects.create(
            customer=request.user.customer_profile,
            artisan=service.artisan,           # derived from the service
            service=service,
            scheduled_for=validated["scheduled_for"],
            location=Point(lng, lat),          # (x=lng, y=lat)
            address=validated["address"],
            notes=validated.get("notes", ""),
            agreed_price=service.base_price,    # snapshot the price at booking time
        )


class BookingStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_email = serializers.EmailField(source="changed_by.email", read_only=True)

    class Meta:
        model = BookingStatusHistory
        fields = ("from_status", "to_status", "changed_by_email", "created_at")


class BookingSerializer(serializers.ModelSerializer):
    """Read representation returned to customers and artisans."""

    customer_email = serializers.EmailField(source="customer.user.email", read_only=True)
    artisan_email = serializers.EmailField(source="artisan.user.email", read_only=True)
    service_title = serializers.CharField(source="service.title", read_only=True)
    location = serializers.SerializerMethodField()
    history = BookingStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id", "status", "service", "service_title",
            "customer", "customer_email", "artisan", "artisan_email",
            "scheduled_for", "location", "address", "agreed_price",
            "notes", "history", "created_at",
        )
        read_only_fields = fields

    def get_location(self, obj):
        p = obj.location
        return {"latitude": p.y, "longitude": p.x} if p else None
