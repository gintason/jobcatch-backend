"""Serializers for the authentication surface."""
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (
    ArtisanProfile,
    CustomerProfile,
    EmployerProfile,
    JobSeekerProfile,
    OTPPurpose,
    User,
    UserRole,
)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
    # Admins cannot self-register.
    role = serializers.ChoiceField(
        choices=[c for c in UserRole.choices if c[0] != UserRole.ADMIN]
    )
    # `phone` is unique but optional: many users register without one. Empty
    # strings would collide on the unique index (only NULLs are exempt), so a
    # blank value is normalised to None.
    phone = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=20
    )

    class Meta:
        model = User
        fields = ("id", "email", "phone", "full_name", "role", "password")
        read_only_fields = ("id",)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_phone(self, value):
        value = (value or "").strip()
        if not value:
            return None
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("This phone number is already registered.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        validated_data["phone"] = validated_data.get("phone") or None
        user = User(**validated_data)
        user.set_password(password)
        user.is_email_verified = False
        user.save()  # post_save signal creates the role profile
        return user


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=4, max_length=10)
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices, default=OTPPurpose.EMAIL_VERIFY)


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=OTPPurpose.choices, default=OTPPurpose.EMAIL_VERIFY)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=4, max_length=10)
    new_password = serializers.CharField(min_length=8, style={"input_type": "password"})

    def validate_new_password(self, value):
        validate_password(value)
        return value


# ------------------------------------------------------------------ profile read
class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ("address",)


class ArtisanProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArtisanProfile
        fields = ("bio", "service_radius_km", "is_available",
                  "avg_rating", "rating_count", "reputation_score", "is_featured")


class EmployerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployerProfile
        fields = ("company_name", "cac_number", "is_cac_verified", "website")


class JobSeekerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSeekerProfile
        fields = ("headline", "skills", "active_cv")


_PROFILE_SERIALIZERS = {
    UserRole.CUSTOMER: ("customer_profile", CustomerProfileSerializer),
    UserRole.ARTISAN: ("artisan_profile", ArtisanProfileSerializer),
    UserRole.EMPLOYER: ("employer_profile", EmployerProfileSerializer),
    UserRole.JOB_SEEKER: ("job_seeker_profile", JobSeekerProfileSerializer),
}


class MeSerializer(serializers.ModelSerializer):
    """Current user + their role-specific profile (nested, read-only for now)."""

    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "phone", "full_name", "role",
                  "is_email_verified", "is_identity_verified", "profile")
        read_only_fields = fields

    def get_profile(self, obj):
        mapping = _PROFILE_SERIALIZERS.get(obj.role)
        if not mapping:
            return None
        attr, serializer_cls = mapping
        profile = getattr(obj, attr, None)
        return serializer_cls(profile).data if profile else None
