from rest_framework import serializers

from apps.catalog.models import Category, CategoryKind
from apps.common.phone import InvalidPhone, to_local
from apps.payments.serializers import GatewayField

from .models import JobAlertSubscription, UpgradePrompt


class UpgradePromptSerializer(serializers.ModelSerializer):
    class Meta:
        model = UpgradePrompt
        fields = ("key", "style", "title", "body", "highlights", "cta_label", "cta_url")


class JobAlertSubscriptionSerializer(serializers.ModelSerializer):
    categories = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Category.objects.filter(kind=CategoryKind.JOB))

    class Meta:
        model = JobAlertSubscription
        fields = ("whatsapp_number", "categories", "keywords", "is_active", "last_sent_at")
        read_only_fields = ("last_sent_at",)

    def validate_whatsapp_number(self, value):
        if not value:
            return value
        try:
            return to_local(value)
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_categories(self, value):
        if len(value) > 10:
            raise serializers.ValidationError("Choose up to 10 categories.")
        return value


class CheckoutSerializer(serializers.Serializer):
    gateway = GatewayField()
