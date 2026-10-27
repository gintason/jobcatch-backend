import os

from rest_framework import serializers

from apps.catalog.models import Category, CategoryKind
from apps.payments.serializers import GatewayField

from .models import ArtisanVerificationApplication, ArtisanVerificationDocument, Verification


class VerificationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Verification
        fields = ("id", "type", "document")
        read_only_fields = ("id",)


class VerificationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True)

    class Meta:
        model = Verification
        fields = ("id", "user", "user_email", "type", "document", "status",
                  "reviewed_by_email", "review_note", "created_at")
        read_only_fields = fields


# ---------------------------------------------------------------- paid artisan verification


class ArtisanVerificationDocumentSerializer(serializers.ModelSerializer):
    doc_type_label = serializers.CharField(source="get_doc_type_display", read_only=True)

    class Meta:
        model = ArtisanVerificationDocument
        fields = ("id", "doc_type", "doc_type_label", "file", "original_name", "created_at")
        read_only_fields = ("id", "original_name", "created_at")
        extra_kwargs = {"file": {"write_only": True}}

    def create(self, validated):
        validated["original_name"] = os.path.basename(validated["file"].name)[:255]
        return super().create(validated)


class ArtisanVerificationSerializer(serializers.ModelSerializer):
    categories = serializers.PrimaryKeyRelatedField(
        many=True, required=False,
        queryset=Category.objects.filter(kind=CategoryKind.HOME_SERVICE),
    )
    category_names = serializers.SerializerMethodField()
    documents = ArtisanVerificationDocumentSerializer(many=True, read_only=True)
    fee_paid = serializers.BooleanField(read_only=True)
    is_editable = serializers.BooleanField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    requirements = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()
    id_number = serializers.CharField(write_only=True, required=False, allow_blank=True,
                                      max_length=30)
    id_number_masked = serializers.SerializerMethodField()

    class Meta:
        model = ArtisanVerificationApplication
        fields = ("id", "status", "status_label", "is_editable", "categories", "category_names",
                  "years_experience", "id_type", "id_number", "id_number_masked",
                  "trade_credentials", "documents", "fee", "fee_paid", "fee_paid_at",
                  "requirements", "submitted_at", "reviewed_at", "review_note")
        read_only_fields = ("id", "status", "fee_paid_at", "submitted_at", "reviewed_at",
                            "review_note")

    def validate_categories(self, value):
        if len(value) > 5:
            raise serializers.ValidationError("Pick up to 5 skill categories.")
        return value

    def validate_years_experience(self, value):
        if value is not None and value > 60:
            raise serializers.ValidationError("Enter your years of experience.")
        return value

    def get_category_names(self, obj):
        return [c.name for c in obj.categories.all()]

    def get_requirements(self, obj):
        from .services import application_requirements

        return application_requirements(obj)

    def get_fee(self, obj):
        from django.conf import settings

        return str(obj.fee_amount or settings.ARTISAN_VERIFICATION_FEE)

    def get_id_number_masked(self, obj):
        n = obj.id_number or ""
        return f"****{n[-4:]}" if len(n) >= 4 else ""


class SubmitArtisanVerificationSerializer(serializers.Serializer):
    gateway = GatewayField()


class AdminArtisanVerificationSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="artisan.user.full_name", read_only=True)
    email = serializers.EmailField(source="artisan.user.email", read_only=True)
    phone = serializers.CharField(source="artisan.user.phone", read_only=True)
    artisan_id = serializers.UUIDField(source="artisan.id", read_only=True)
    category_names = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    payment_reference = serializers.CharField(source="payment.reference", read_only=True,
                                              default=None)
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True,
                                               default=None)
    history = serializers.SerializerMethodField()

    class Meta:
        model = ArtisanVerificationApplication
        fields = ("id", "status", "full_name", "email", "phone", "artisan_id",
                  "category_names", "years_experience", "id_type", "id_number",
                  "trade_credentials", "documents", "fee_amount", "fee_paid_at",
                  "payment_reference", "submitted_at", "reviewed_at", "reviewed_by_email",
                  "review_note", "history")

    def get_category_names(self, obj):
        return [c.name for c in obj.categories.all()]

    def get_documents(self, obj):
        return [{"id": str(d.id), "doc_type": d.doc_type, "doc_type_label": d.get_doc_type_display(),
                 "original_name": d.original_name, "created_at": d.created_at}
                for d in obj.documents.all()]

    def get_history(self, obj):
        from apps.common.audit import history

        return [{"action": e.action, "actor": e.actor.email if e.actor else "system",
                 "metadata": e.metadata, "created_at": e.created_at} for e in history(obj)]
