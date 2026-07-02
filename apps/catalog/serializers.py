from rest_framework import serializers

from .models import Category, Service


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "parent")
        read_only_fields = ("id", "slug")


class ServiceSerializer(serializers.ModelSerializer):
    artisan_email = serializers.EmailField(source="artisan.user.email", read_only=True)

    class Meta:
        model = Service
        fields = (
            "id", "category", "title", "description", "base_price",
            "is_active", "artisan", "artisan_email", "created_at",
        )
        # artisan is derived from the authenticated user, never client-supplied.
        read_only_fields = ("id", "artisan", "artisan_email", "created_at")
