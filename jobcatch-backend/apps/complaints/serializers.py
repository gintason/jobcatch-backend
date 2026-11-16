import os

from rest_framework import serializers

from apps.accounts.models import ArtisanProfile
from apps.common.phone import InvalidPhone, to_local

from .models import Complaint, ComplaintCategory


class ComplaintCreateSerializer(serializers.ModelSerializer):
    artisan = serializers.PrimaryKeyRelatedField(queryset=ArtisanProfile.objects.all(),
                                                 required=False, allow_null=True)
    # Honeypot: real users never see or fill this field.
    website = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Complaint
        fields = ("category", "full_name", "email", "phone", "subject", "description",
                  "incident_date", "artisan", "artisan_name", "transaction_reference",
                  "attachment", "website")

    def validate_phone(self, value):
        try:
            return to_local(value)
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_description(self, value):
        value = value.replace("\r\n", "\n").strip()
        if len(value) < 20:
            raise serializers.ValidationError("Please describe what happened (at least 20 characters).")
        return value[:5000]

    def validate(self, data):
        if data.pop("website", ""):
            raise serializers.ValidationError("Could not submit the complaint.")
        if data["category"] == ComplaintCategory.ARTISAN and not (
                data.get("artisan") or data.get("artisan_name", "").strip()):
            raise serializers.ValidationError({"artisan_name": "Tell us which artisan this is about."})
        if data["category"] in (ComplaintCategory.BILLING, ComplaintCategory.PAYMENT) and not (
                data.get("transaction_reference", "").strip()):
            raise serializers.ValidationError(
                {"transaction_reference": "Add the receipt or transaction reference so we can find it."})
        return data


class ComplaintPublicSerializer(serializers.ModelSerializer):
    """What the complainant sees (no internal notes)."""

    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Complaint
        fields = ("id", "reference", "category", "category_label", "subject", "status",
                  "status_label", "admin_response", "responded_at", "resolved_at", "created_at")


class ComplaintAdminSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    artisan_full_name = serializers.CharField(source="artisan.user.full_name", read_only=True,
                                              default=None)
    has_attachment = serializers.SerializerMethodField()
    attachment_name = serializers.SerializerMethodField()
    handled_by_email = serializers.EmailField(source="handled_by.email", read_only=True, default=None)
    history = serializers.SerializerMethodField()
    linked_record = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = ("id", "reference", "category", "category_label", "full_name", "email", "phone",
                  "subject", "description", "incident_date", "artisan", "artisan_full_name",
                  "artisan_name", "transaction_reference", "linked_record", "has_attachment",
                  "attachment_name",
                  "status", "status_label", "admin_response", "internal_note", "handled_by_email",
                  "responded_at", "resolved_at", "created_at", "history")

    def get_has_attachment(self, obj):
        return bool(obj.attachment)

    def get_attachment_name(self, obj):
        return os.path.basename(obj.attachment.name) if obj.attachment else None

    def get_linked_record(self, obj):
        """Resolve the reference the complainant gave to the bill order / payment it names."""
        from .services import find_transaction

        return find_transaction(obj.transaction_reference)

    def get_history(self, obj):
        from apps.common.audit import history

        return [{"action": e.action, "actor": e.actor.email if e.actor else "system",
                 "metadata": e.metadata, "created_at": e.created_at} for e in history(obj)]
