from rest_framework import serializers

from .models import Category, Service


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "kind", "parent")
        read_only_fields = ("id", "slug")


class ServiceSerializer(serializers.ModelSerializer):
    def validate_category(self, value):
        from .models import CategoryKind

        if value.kind != CategoryKind.HOME_SERVICE:
            raise serializers.ValidationError(
                "Services must use a home-service category."
            )
        return value

    artisan_email = serializers.EmailField(source="artisan.user.email", read_only=True)

    class Meta:
        model = Service
        fields = (
            "id", "category", "title", "description", "base_price",
            "is_active", "artisan", "artisan_email", "created_at",
        )
        # artisan is derived from the authenticated user, never client-supplied.
        read_only_fields = ("id", "artisan", "artisan_email", "created_at")
