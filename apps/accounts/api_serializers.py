"""Serializers for self-service profile management (Phase 2A)."""
from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import (
    ArtisanJobVideo,
    ArtisanPortfolioItem,
    ArtisanProfile,
    CustomerProfile,
    EmployerProfile,
    JobSeekerProfile,
    User,
)


class _LocationMixin(serializers.ModelSerializer):
    """Read/write a PointField as latitude/longitude pair."""

    latitude = serializers.FloatField(write_only=True, required=False)
    longitude = serializers.FloatField(write_only=True, required=False)
    location = serializers.SerializerMethodField()

    LOCATION_FIELD = "location"  # subclasses whose point attr differs override this

    def get_location(self, obj):
        point = getattr(obj, self.LOCATION_FIELD, None)
        return {"latitude": point.y, "longitude": point.x} if point else None

    def _apply_point(self, validated):
        lat = validated.pop("latitude", None)
        lng = validated.pop("longitude", None)
        if lat is not None and lng is not None:
            validated[self.LOCATION_FIELD] = Point(lng, lat)  # (x=lng, y=lat)
        return validated

    def update(self, instance, validated_data):
        validated_data = self._apply_point(validated_data)
        return super().update(instance, validated_data)


class CustomerProfileSerializer(_LocationMixin):
    class Meta:
        model = CustomerProfile
        fields = ("address", "latitude", "longitude", "location")


class ArtisanProfileSerializer(_LocationMixin):
    LOCATION_FIELD = "base_location"
    location = serializers.SerializerMethodField()
    # Phone lives on User, but artisans manage it from their profile screen so
    # customers can call them directly.
    phone = serializers.CharField(
        source="user.phone", required=False, allow_blank=True,
        allow_null=True, max_length=20,
    )

    class Meta:
        model = ArtisanProfile
        fields = (
            "bio", "phone", "show_phone", "service_radius_km", "is_available",
            "city", "area", "state",
            "latitude", "longitude", "location",
            # read-only, system-managed:
            "avg_rating", "rating_count", "reputation_score",
            "is_featured", "is_work_verified",
        )
        read_only_fields = (
            "avg_rating", "rating_count", "reputation_score",
            "is_featured", "is_work_verified",
        )

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        phone = user_data.get("phone")
        if "phone" in user_data:
            phone = (phone or "").strip() or None
            if phone and User.objects.filter(phone=phone).exclude(pk=instance.user_id).exists():
                raise serializers.ValidationError(
                    {"phone": "This phone number is already registered."}
                )
            instance.user.phone = phone
            instance.user.save(update_fields=["phone", "updated_at"])
        return super().update(instance, validated_data)


class EmployerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployerProfile
        fields = ("company_name", "cac_number", "website", "is_cac_verified")
        read_only_fields = ("is_cac_verified",)


class JobSeekerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSeekerProfile
        fields = (
            "headline", "skills", "active_cv",
            "is_graduate", "nysc_status", "nysc_certificate",
        )


# Maps role -> (profile attribute on user, serializer)
PROFILE_MAP = {
    "customer": ("customer_profile", CustomerProfileSerializer),
    "artisan": ("artisan_profile", ArtisanProfileSerializer),
    "employer": ("employer_profile", EmployerProfileSerializer),
    "job_seeker": ("job_seeker_profile", JobSeekerProfileSerializer),
}


class ArtisanPortfolioItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArtisanPortfolioItem
        fields = ("id", "image", "caption", "created_at")
        read_only_fields = ("id", "created_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.image:
            # Relative /media/... path so the frontend loads it through its proxy
            # (avoids http/https mixed-content behind the Codespaces TLS proxy).
            data["image"] = instance.image.url
        return data


class ArtisanJobVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArtisanJobVideo
        fields = ("id", "video", "title", "description",
                  "status", "review_note", "created_at")
        # status is set by admins during verification, not by the artisan.
        read_only_fields = ("id", "status", "review_note", "created_at")
