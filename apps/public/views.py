"""
Public, unauthenticated endpoints for the home page + browse/profile pages.

Non-sensitive showcase data only (categories, artisan cards, and public profiles
with services + portfolio). Exact coordinates and contact details are not exposed.
"""
from django.db.models import Count, Q
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny

from apps.accounts.models import ArtisanProfile
from apps.catalog.models import Category

from .serializers import (
    PublicArtisanDetailSerializer,
    PublicArtisanSerializer,
    PublicCategorySerializer,
)


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
        return (
            ArtisanProfile.objects
            .select_related("user")
            .prefetch_related("services__category", "portfolio")
            .order_by("-is_featured", "-avg_rating", "-rating_count", "-created_at")[:8]
        )


class ArtisanBrowseList(ListAPIView):
    """Search + filter artisans (paginated). ?q= and ?category=<slug|name>."""

    permission_classes = [AllowAny]
    serializer_class = PublicArtisanSerializer

    def get_queryset(self):
        qs = (ArtisanProfile.objects
              .select_related("user")
              .prefetch_related("services__category", "portfolio"))
        category = self.request.query_params.get("category")
        q = self.request.query_params.get("q")
        if category:
            qs = qs.filter(
                Q(services__category__slug=category) | Q(services__category__name__iexact=category)
            ).distinct()
        if q:
            qs = qs.filter(
                Q(user__full_name__icontains=q)
                | Q(bio__icontains=q)
                | Q(services__title__icontains=q)
                | Q(services__category__name__icontains=q)
            ).distinct()
        return qs.order_by("-is_featured", "-avg_rating", "-rating_count", "-created_at")


class ArtisanDetail(RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicArtisanDetailSerializer
    lookup_field = "id"

    def get_queryset(self):
        return (ArtisanProfile.objects
                .select_related("user")
                .prefetch_related("services__category", "portfolio"))
