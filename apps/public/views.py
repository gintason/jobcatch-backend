"""
Public, unauthenticated endpoints for the marketing home page.

These expose only non-sensitive, showcase data (categories + top artisans) so a
logged-out visitor can browse before signing up. Exact locations and contact
details are never exposed here.
"""
from django.db.models import Count, Q
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from apps.accounts.models import ArtisanProfile
from apps.catalog.models import Category

from .serializers import PublicArtisanSerializer, PublicCategorySerializer


class PublicCategoryList(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.annotate(
            service_count=Count("services", filter=Q(services__is_active=True))
        ).order_by("name")


class FeaturedArtisanList(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicArtisanSerializer
    pagination_class = None

    def get_queryset(self):
        # Show real registered artisans; featured + top-rated first. Not filtered
        # to verified-only so the section isn't empty while the platform is new.
        return (
            ArtisanProfile.objects
            .select_related("user")
            .prefetch_related("services__category", "portfolio")
            .order_by("-is_featured", "-avg_rating", "-rating_count", "-created_at")[:8]
        )
