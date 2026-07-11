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
    other_party = serializers.SerializerMethodField()
    context_label = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ("id", "participant_emails", "other_party", "context_label",
                  "booking", "job", "created_at")
        read_only_fields = fields

    def get_participant_emails(self, obj):
        return [u.email for u in obj.participants.all()]

    def get_other_party(self, obj):
        """Name of the participant who isn't the caller."""
        request = self.context.get("request")
        if not request:
            return ""
        other = next((u for u in obj.participants.all() if u != request.user), None)
        return other.full_name if other else ""

    def get_context_label(self, obj):
        if obj.job_id:
            return f"Job: {obj.job.title}"
        if obj.booking_id:
            return f"Booking: {obj.booking.service.title}"
        return "Direct message"


class ConversationCreateSerializer(serializers.Serializer):
    participant = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all(), required=False)
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all(), required=False)

    def validate_participant(self, other):
        if other == self.context["request"].user:
            raise serializers.ValidationError("You cannot start a conversation with yourself.")
        return other
