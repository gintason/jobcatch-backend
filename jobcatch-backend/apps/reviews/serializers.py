from rest_framework import serializers

from apps.bookings.models import BookingStatus
from apps.bookings.services import can_review

from .models import Review


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ("id", "booking", "rating", "comment")
        read_only_fields = ("id",)

    def validate(self, data):
        request = self.context["request"]
        booking = data["booking"]
        if booking.customer.user != request.user:
            raise serializers.ValidationError("You can only review your own bookings.")
        if booking.status != BookingStatus.COMPLETED:
            raise serializers.ValidationError("You can only review a completed booking.")
        if not can_review(booking):
            raise serializers.ValidationError("This booking has already been reviewed.")
        return data

    def create(self, validated):
        request = self.context["request"]
        booking = validated["booking"]
        return Review.objects.create(
            booking=booking,
            author=request.user,
            target=booking.artisan.user,       # the artisan being reviewed
            rating=validated["rating"],
            comment=validated.get("comment", ""),
        )


class ReviewSerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True)
    target_email = serializers.EmailField(source="target.email", read_only=True)

    class Meta:
        model = Review
        fields = ("id", "booking", "author_email", "target_email",
                  "rating", "comment", "created_at")
        read_only_fields = fields
