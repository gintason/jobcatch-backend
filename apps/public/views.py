"""
Public, unauthenticated endpoints for the home page + browse/profile pages.

Listings are ranked by the owner's active subscription tier (premium > pro >
free/none) so paid placements surface first, then by rating/recency.
"""
from django.db.models import (
    Case, Count, IntegerField, OuterRef, Q, Subquery, When,
)
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny

from apps.accounts.models import ArtisanProfile
from apps.catalog.models import Category
from apps.jobs.models import Job
from apps.subscriptions.models import Subscription

from .serializers import (
    PublicArtisanDetailSerializer,
    PublicArtisanSerializer,
    PublicCategorySerializer,
    PublicJobSerializer,
)


def _tier_case():
    """0 = premium (top), 1 = pro, 2 = free/none (bottom)."""
    return Case(
        When(plan="premium", then=0),
        When(plan="pro", then=1),
        default=2,
        output_field=IntegerField(),
    )


def _active_plan_for(outer_field):
    """Subquery: the given user's active subscription plan (latest first)."""
    return Subscription.objects.filter(
        user=OuterRef(outer_field), is_active=True
    ).order_by("-started_at").values("plan")[:1]


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
            .annotate(plan=Subquery(_active_plan_for("user")))
            .annotate(tier=_tier_case())
            .order_by("tier", "-avg_rating", "-rating_count", "-created_at")[:8]
        )


class ArtisanBrowseList(ListAPIView):
    """Search + filter artisans (paginated). ?q= and ?category=<slug|name>."""

    permission_classes = [AllowAny]
    serializer_class = PublicArtisanSerializer

    def get_queryset(self):
        qs = (ArtisanProfile.objects
              .select_related("user")
              .prefetch_related("services__category", "portfolio")
              .annotate(plan=Subquery(_active_plan_for("user")))
              .annotate(tier=_tier_case()))
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
        return qs.order_by("tier", "-avg_rating", "-rating_count", "-created_at")


class ArtisanDetail(RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicArtisanDetailSerializer
    lookup_field = "id"

    def get_queryset(self):
        return (ArtisanProfile.objects
                .select_related("user")
                .prefetch_related("services__category", "portfolio"))


class FeaturedJobList(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicJobSerializer
    pagination_class = None

    def get_queryset(self):
        return (
            Job.objects.filter(is_open=True)
            .select_related("employer__user", "category")
            .annotate(plan=Subquery(_active_plan_for("employer__user")))
            .annotate(tier=_tier_case())
            .order_by("tier", "-created_at")[:6]
        )


class PublicJobBrowse(ListAPIView):
    """All open jobs (paginated), tier-ranked. ?q= and ?category=<slug|name>."""

    permission_classes = [AllowAny]
    serializer_class = PublicJobSerializer

    def get_queryset(self):
        qs = (Job.objects.filter(is_open=True)
              .select_related("employer__user", "category")
              .annotate(plan=Subquery(_active_plan_for("employer__user")))
              .annotate(tier=_tier_case()))
        category = self.request.query_params.get("category")
        q = self.request.query_params.get("q")
        if category:
            qs = qs.filter(Q(category__slug=category) | Q(category__name__iexact=category))
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        return qs.order_by("tier", "-created_at")
