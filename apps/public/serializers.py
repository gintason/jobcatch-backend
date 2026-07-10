"""Public (unauthenticated) serializers for the marketing home page."""
from rest_framework import serializers

from apps.accounts.models import ArtisanProfile
from apps.catalog.models import Category


class PublicCategorySerializer(serializers.ModelSerializer):
    service_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "service_count")


class PublicArtisanSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    trade = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    is_verified = serializers.SerializerMethodField()

    class Meta:
        model = ArtisanProfile
        fields = ("id", "full_name", "trade", "image", "avg_rating",
                  "rating_count", "is_featured", "is_verified")

    def get_trade(self, obj):
        svc = obj.services.filter(is_active=True).select_related("category").first()
        if svc and svc.category:
            return svc.category.name
        if svc:
            return svc.title
        return "Service Professional"

    def get_image(self, obj):
        item = obj.portfolio.first()
        if item and item.image:
            # Relative /media/... path so the frontend loads it through its proxy.
            return item.image.url
        return None

    def get_is_verified(self, obj):
        return bool(obj.is_work_verified or obj.user.is_identity_verified)
