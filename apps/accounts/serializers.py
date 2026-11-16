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
    # Optional: one account works across every role (users switch at the hub);
    # new accounts start as a customer, like Google sign-ups.
    role = serializers.ChoiceField(
        choices=[c for c in UserRole.choices if c[0] != UserRole.ADMIN],
        required=False, default=UserRole.CUSTOMER,
    )
    # `phone` is unique but optional: many users register without one. Empty
    # strings would collide on the unique index (only NULLs are exempt), so a
    # blank value is normalised to None.
    phone = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=20
    )

    # Optional invite code from a friend (referrals app).
    referral_code = serializers.CharField(required=False, allow_blank=True, write_only=True, max_length=12)

    class Meta:
        model = User
        fields = ("id", "email", "phone", "full_name", "role", "password", "referral_code")
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

    def validate_referral_code(self, value):
        from apps.referrals.services import find_referrer
        value = (value or "").strip().upper()
        if value and find_referrer(value) is None:
            raise serializers.ValidationError("That referral code isn't valid.")
        return value

    def create(self, validated_data):
        referral_code = validated_data.pop("referral_code", "")
        password = validated_data.pop("password")
        validated_data["phone"] = validated_data.get("phone") or None
        user = User(**validated_data)
        user.set_password(password)
        user.is_email_verified = False
        user.save()  # post_save signal creates the role profile
        if referral_code:
            from apps.referrals.services import attach
            attach(user, referral_code)
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
    photo = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "phone", "full_name", "role", "photo", "has_password", "date_joined",
                  "is_email_verified", "is_phone_verified", "is_identity_verified", "profile")
        read_only_fields = fields

    def get_photo(self, obj):
        return obj.photo.url if obj.photo else None

    def get_has_password(self, obj):
        return obj.has_usable_password()

    def get_profile(self, obj):
        mapping = _PROFILE_SERIALIZERS.get(obj.role)
        if not mapping:
            return None
        attr, serializer_cls = mapping
        profile = getattr(obj, attr, None)
        return serializer_cls(profile).data if profile else None


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """PATCH /auth/me/ — the parts of an account a user may change themselves."""

    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20)
    photo = serializers.FileField(required=False, allow_null=True)
    remove_photo = serializers.BooleanField(required=False, write_only=True)

    class Meta:
        model = User
        fields = ("full_name", "phone", "photo", "remove_photo")

    def validate_full_name(self, value):
        value = " ".join((value or "").split())
        if len(value) < 2:
            raise serializers.ValidationError("Enter your full name.")
        return value[:150]

    def validate_phone(self, value):
        digits = "".join(c for c in (value or "") if c.isdigit() or c == "+")
        if not digits:
            return None
        bare = digits.lstrip("+")
        if not (10 <= len(bare) <= 14):
            raise serializers.ValidationError("Enter a valid phone number, e.g. 0803 123 4567.")
        if User.objects.filter(phone=digits).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This phone number is already used by another account.")
        return digits

    def validate_photo(self, value):
        if value is None:
            return value
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Choose a photo under 5 MB.")
        from apps.common.validators import validate_image_upload
        validate_image_upload(value)
        head = value.read(16)
        value.seek(0)
        is_image = (head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n")
                    or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[4:8] == b"ftyp")
        if not is_image:
            raise serializers.ValidationError("That file isn't a photo. Use a JPEG, PNG or WebP image.")
        return value

    def update(self, instance, validated):
        old = instance.photo.name if instance.photo else ""
        storage = User._meta.get_field("photo").storage
        if validated.pop("remove_photo", False) and "photo" not in validated:
            validated["photo"] = None
        if "phone" in validated and validated["phone"] != instance.phone:
            instance.is_phone_verified = False          # a new number must be re-verified
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        if old and old != (instance.photo.name if instance.photo else ""):
            storage.delete(old)                         # don't leave replaced photos behind
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    new_password = serializers.CharField(min_length=8, write_only=True)

    def validate(self, data):
        user = self.context["request"].user
        # Google-only accounts have no password yet and may set one directly.
        if user.has_usable_password() and not user.check_password(data.get("current_password") or ""):
            raise serializers.ValidationError({"current_password": "Your current password is incorrect."})
        validate_password(data["new_password"], user)
        if user.has_usable_password() and data.get("current_password") == data["new_password"]:
            raise serializers.ValidationError({"new_password": "Choose a password you haven't used here."})
        return data
