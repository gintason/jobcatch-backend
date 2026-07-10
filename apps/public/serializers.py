"""Public (unauthenticated) serializers for the marketing + browse pages."""
from rest_framework import serializers

from apps.accounts.models import ArtisanPortfolioItem, ArtisanProfile
from apps.catalog.models import Category, Service
from apps.jobs.models import Job


class PublicCategorySerializer(serializers.ModelSerializer):
    service_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "service_count")


class PublicArtisanSerializer(serializers.ModelSerializer):
    """Card representation for lists (featured + browse)."""

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
        return item.image.url if item and item.image else None

    def get_is_verified(self, obj):
        return bool(obj.is_work_verified or obj.user.is_identity_verified)


class PublicServiceSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Service
        fields = ("id", "title", "category_name", "description", "base_price")


class PublicPortfolioSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ArtisanPortfolioItem
        fields = ("id", "image", "caption")

    def get_image(self, obj):
        return obj.image.url if obj.image else None


class PublicReviewSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    author_name = serializers.CharField(source="author.full_name", read_only=True)
    rating = serializers.IntegerField(read_only=True)
    comment = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class PublicArtisanDetailSerializer(serializers.ModelSerializer):
    """Full public profile: bio, services, and portfolio gallery."""

    full_name = serializers.CharField(source="user.full_name", read_only=True)
    is_verified = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()
    portfolio = PublicPortfolioSerializer(many=True, read_only=True)
    reviews = serializers.SerializerMethodField()

    class Meta:
        model = ArtisanProfile
        fields = ("id", "full_name", "bio", "avg_rating", "rating_count",
                  "is_featured", "is_verified", "service_radius_km",
                  "services", "portfolio", "reviews")

    def get_is_verified(self, obj):
        return bool(obj.is_work_verified or obj.user.is_identity_verified)

    def get_services(self, obj):
        active = obj.services.filter(is_active=True).select_related("category")
        return PublicServiceSerializer(active, many=True).data

    def get_reviews(self, obj):
        qs = obj.user.reviews_received.filter(is_visible=True).select_related("author")[:20]
        return PublicReviewSerializer(qs, many=True).data


class PublicJobSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    employer_company = serializers.CharField(source="employer.company_name", read_only=True)

    class Meta:
        model = Job
        fields = ("id", "title", "description", "category", "category_name",
                  "employer_company", "salary_min", "salary_max", "created_at")
