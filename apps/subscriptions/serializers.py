from rest_framework import serializers

from .models import Subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ("id", "plan", "is_active", "started_at", "expires_at",
                  "auto_renew", "created_at")
        read_only_fields = fields
