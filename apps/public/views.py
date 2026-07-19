"""
Public, unauthenticated endpoints for the home page + browse/profile pages.

Listings are ranked by the owner's active subscription tier (premium > pro >
free/none) so paid placements surface first, then by verification, then by
rating/recency.

IMPORTANT — who counts as an artisan:
JobCatch is a single-account platform. Every user is given all four role
profiles on registration so they can switch modes freely, which means an
ArtisanProfile row existing says nothing about whether that person actually
offers services. A customer, employer or job seeker who merely glanced at the
Artisan role would otherwise appear in browse, search, featured listings and
the hero stats.

So every public artisan listing is filtered through `_real_artisans()`: only
profiles with at least one ACTIVE service are treated as artisans. Publishing a
service is the deliberate act that makes someone an artisan on the platform.
"""
from django.db.models import (
    Avg, Case, Count, IntegerField, OuterRef, Q, Subquery, Sum, When,
)
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ArtisanProfile
from apps.catalog.models import Category, CategoryKind
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


def _real_artisans():
    """
    Base queryset for anyone the public should see as an artisan.

    An ArtisanProfile alone isn't enough — every account has one. Only people
    who have published at least one active service are listed.
    """
    return (
        ArtisanProfile.objects
        .filter(services__is_active=True)
        .select_related("user")
        .prefetch_related("services__category", "portfolio")
        .annotate(plan=Subquery(_active_plan_for("user")))
        .annotate(tier=_tier_case())
        .distinct()
    )


# Paid placement first, then verified, then reputation. `is_work_verified`
# covers JobCatch's own work verification; identity verification lives on the
# user and is included so both badges lift a listing.
ARTISAN_ORDERING = (
    "tier",
    "-is_work_verified",
    "-user__is_identity_verified",
    "-avg_rating",
    "-rating_count",
    "-created_at",
)


class PublicCategoryList(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCategorySerializer
    pagination_class = None

    def get_queryset(self):
        qs = Category.objects.annotate(
            service_count=Count("services", filter=Q(services__is_active=True))
        )
        # Home page shows home services; the jobs page shows job categories.
        kind = self.request.query_params.get("kind", "home_service")
        if kind != "all":
            qs = qs.filter(kind=kind)
        return qs.order_by("name")


class FeaturedArtisanList(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicArtisanSerializer
    pagination_class = None

    def get_queryset(self):
        return _real_artisans().order_by(*ARTISAN_ORDERING)[:8]


class ArtisanBrowseList(ListAPIView):
    """Search + filter artisans (paginated). ?q= and ?category=<slug|name>."""

    permission_classes = [AllowAny]
    serializer_class = PublicArtisanSerializer

    def get_queryset(self):
        qs = _real_artisans()
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
        return qs.order_by(*ARTISAN_ORDERING)


class ArtisanDetail(RetrieveAPIView):
    """
    A single artisan's public profile.

    Deliberately NOT filtered by `_real_artisans()`: an artisan who temporarily
    deactivates their last service should still be reachable by anyone holding
    a direct link (e.g. from an existing booking or a shared URL). They simply
    stop appearing in browse and featured listings.
    """

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


class PublicStats(APIView):
    """
    Real social proof for the hero: genuine counts, genuine ratings, genuine
    artisans. Placeholder numbers ("2,000+ verified artisans") are a lie to
    visitors and corrosive on a marketplace whose whole product is trust, so
    everything here is computed from the database. Small numbers are reported
    honestly; the frontend decides how to present them.

    Counts use `_real_artisans()` for the same reason the listings do — every
    account owns an ArtisanProfile, so counting rows would badly overstate how
    many artisans actually offer services.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        artisans = _real_artisans()
        verified = artisans.filter(
            Q(is_work_verified=True) | Q(user__is_identity_verified=True)
        )

        rated = artisans.filter(rating_count__gt=0)
        agg = rated.aggregate(
            avg=Avg("avg_rating"),
            reviews=Sum("rating_count"),
        )

        # Spotlight: verified and paid artisans first — the ones we're happy to
        # put a face to. Only those with a portfolio photo, since the hero cards
        # are visual.
        spotlight = (
            _real_artisans()
            .filter(portfolio__isnull=False)
            .distinct()
            .order_by(*ARTISAN_ORDERING)[:4]
        )

        return Response({
            "artisan_count": artisans.count(),
            "verified_count": verified.count(),
            "review_count": agg["reviews"] or 0,
            "avg_rating": round(float(agg["avg"]), 1) if agg["avg"] else None,
            "spotlight": PublicArtisanSerializer(
                spotlight, many=True, context={"request": request}
            ).data,
        })
