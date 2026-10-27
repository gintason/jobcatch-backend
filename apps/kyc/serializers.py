import os
import re

from rest_framework import serializers

from apps.common.audit import history
from apps.common.phone import InvalidPhone, mask, to_local

from .models import EmployerKYC, KYCDocument
from .services import can_submit, requirements


def _mask_nin(nin):
    return f"*******{nin[-4:]}" if nin and len(nin) >= 4 else ""


class KYCDocumentSerializer(serializers.ModelSerializer):
    doc_type_label = serializers.CharField(source="get_doc_type_display", read_only=True)

    class Meta:
        model = KYCDocument
        fields = ("id", "doc_type", "doc_type_label", "file", "original_name",
                  "status", "review_note", "created_at")
        read_only_fields = ("id", "original_name", "status", "review_note", "created_at")
        extra_kwargs = {"file": {"write_only": True}}

    def create(self, validated):
        validated["original_name"] = os.path.basename(validated["file"].name)[:255]
        return super().create(validated)


class EmployerKYCSerializer(serializers.ModelSerializer):
    """The employer's own view: sensitive identifiers come back masked."""

    director_nin = serializers.CharField(write_only=True, required=False, allow_blank=True)
    director_nin_masked = serializers.SerializerMethodField()
    is_email_verified = serializers.BooleanField(read_only=True)
    is_phone_verified = serializers.BooleanField(read_only=True)
    is_editable = serializers.BooleanField(read_only=True)
    documents = KYCDocumentSerializer(many=True, read_only=True)
    requirements = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    checks = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = EmployerKYC
        fields = (
            "id", "status", "status_label", "is_editable",
            "registered_name", "company_type", "cac_number", "tin",
            "director_name", "director_nin", "director_nin_masked",
            "business_address", "state", "contact_email", "contact_phone",
            "is_email_verified", "is_phone_verified",
            "checks", "documents", "requirements", "can_submit",
            "submitted_at", "reviewed_at", "rejection_reason",
        )
        read_only_fields = ("id", "status", "submitted_at", "reviewed_at", "rejection_reason")

    def get_director_nin_masked(self, obj):
        return _mask_nin(obj.director_nin)

    def get_requirements(self, obj):
        return requirements(obj)

    def get_can_submit(self, obj):
        return can_submit(obj)

    def get_checks(self, obj):
        # Owners see the outcome and message, not the raw provider data.
        return {k: {"status": v.get("status"), "message": v.get("message")}
                for k, v in (obj.checks or {}).items()}

    # ---- validation ----
    def validate_cac_number(self, value):
        value = re.sub(r"^(RC|BN|IT)\s*", "", value.strip().upper())
        if value and not value.isdigit():
            raise serializers.ValidationError("Enter the registration number digits, e.g. 1234567.")
        return value

    def validate_tin(self, value):
        value = value.strip()
        if value and not re.fullmatch(r"\d{8}-?\d{4}|\d{10,13}", value):
            raise serializers.ValidationError("Enter a valid TIN, e.g. 12345678-0001.")
        return value

    def validate_director_nin(self, value):
        value = re.sub(r"\D", "", value or "")
        if value and len(value) != 11:
            raise serializers.ValidationError("A NIN has exactly 11 digits.")
        return value

    def validate_contact_phone(self, value):
        if not value:
            return value
        try:
            return to_local(value)
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc


class AdminKYCListSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="employer.company_name", read_only=True)
    user_email = serializers.EmailField(source="employer.user.email", read_only=True)
    check_summary = serializers.SerializerMethodField()
    document_count = serializers.IntegerField(source="documents.count", read_only=True)

    class Meta:
        model = EmployerKYC
        fields = ("id", "status", "company_name", "registered_name", "user_email",
                  "cac_number", "check_summary", "document_count",
                  "submitted_at", "reviewed_at", "is_manual_override")

    def get_check_summary(self, obj):
        return {k: v.get("status") for k, v in (obj.checks or {}).items()}


class AuditEntrySerializer(serializers.Serializer):
    action = serializers.CharField()
    actor = serializers.SerializerMethodField()
    metadata = serializers.JSONField()
    ip_address = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()

    def get_actor(self, obj):
        return obj.actor.email if obj.actor else "system"


class AdminKYCDetailSerializer(serializers.ModelSerializer):
    """Full case file for reviewers: unmasked identifiers, raw check data, history."""

    company_name = serializers.CharField(source="employer.company_name", read_only=True)
    user_email = serializers.EmailField(source="employer.user.email", read_only=True)
    user_name = serializers.CharField(source="employer.user.full_name", read_only=True)
    contact_phone_masked = serializers.SerializerMethodField()
    is_email_verified = serializers.BooleanField(read_only=True)
    is_phone_verified = serializers.BooleanField(read_only=True)
    documents = serializers.SerializerMethodField()
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True,
                                               default=None)
    history = serializers.SerializerMethodField()

    class Meta:
        model = EmployerKYC
        fields = (
            "id", "status", "company_name", "user_email", "user_name",
            "registered_name", "company_type", "cac_number", "tin",
            "director_name", "director_nin", "business_address", "state",
            "contact_email", "contact_phone", "contact_phone_masked",
            "is_email_verified", "is_phone_verified",
            "email_verified_at", "phone_verified_at",
            "checks", "documents", "submitted_at", "reviewed_at", "reviewed_by_email",
            "rejection_reason", "admin_note", "is_manual_override", "history",
        )

    def get_contact_phone_masked(self, obj):
        return mask(obj.contact_phone)

    def get_documents(self, obj):
        return [
            {"id": str(d.id), "doc_type": d.doc_type, "doc_type_label": d.get_doc_type_display(),
             "original_name": d.original_name, "status": d.status,
             "review_note": d.review_note, "created_at": d.created_at}
            for d in obj.documents.all()
        ]

    def get_history(self, obj):
        return AuditEntrySerializer(history(obj), many=True).data
