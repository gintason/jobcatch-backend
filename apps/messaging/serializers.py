from rest_framework import serializers

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.jobs.models import Job

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    sender_email = serializers.EmailField(source="sender.email", read_only=True)

    class Meta:
        model = Message
        fields = ("id", "sender_email", "body", "attachment", "read_at", "created_at")
        read_only_fields = fields


class MessageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ("id", "body", "attachment")
        read_only_fields = ("id",)


class ConversationSerializer(serializers.ModelSerializer):
    participant_emails = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ("id", "participant_emails", "booking", "job", "created_at")
        read_only_fields = fields

    def get_participant_emails(self, obj):
        return [u.email for u in obj.participants.all()]


class ConversationCreateSerializer(serializers.Serializer):
    participant = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all(), required=False)
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all(), required=False)

    def validate_participant(self, other):
        if other == self.context["request"].user:
            raise serializers.ValidationError("You cannot start a conversation with yourself.")
        return other
