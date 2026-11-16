from rest_framework import serializers

from apps.payments.serializers import GatewayField

from .models import JobPackage, PackagePurchase


class JobPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobPackage
        fields = ("id", "name", "slug", "description", "price", "job_credits", "listing_days",
                  "validity_days", "is_featured", "features", "highlight_label")


class PackagePurchaseSerializer(serializers.ModelSerializer):
    package_name = serializers.CharField(source="package.name", read_only=True)
    credits_remaining = serializers.IntegerField(read_only=True)
    reference = serializers.CharField(source="payment.reference", read_only=True, default=None)
    invoice_number = serializers.CharField(source="payment.invoice_number", read_only=True,
                                           default=None)

    class Meta:
        model = PackagePurchase
        fields = ("id", "package", "package_name", "status", "price", "credits_total",
                  "credits_used", "credits_remaining", "listing_days", "is_featured",
                  "activated_at", "expires_at", "reference", "invoice_number", "created_at")
        read_only_fields = fields


class PurchaseRequestSerializer(serializers.Serializer):
    package = serializers.PrimaryKeyRelatedField(queryset=JobPackage.objects.filter(is_active=True))
    job = serializers.UUIDField(required=False, allow_null=True)
    gateway = GatewayField()
