from rest_framework import serializers

from .models import Verification


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
