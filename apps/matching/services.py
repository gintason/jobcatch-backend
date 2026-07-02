"""
Artisan matching engine.

Two-stage: (1) PostGIS filters candidates to those within the customer's search
radius AND within each artisan's own service radius; (2) a weighted scorer ranks
them by proximity, rating, featured status, and verification.

Deterministic and tunable via settings.MATCHING_WEIGHTS. The scoring interface is
intentionally isolated so it can later be swapped for a learned ranker.
"""
from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D

from apps.accounts.models import ArtisanProfile


def _score(artisan, distance_km, radius_km):
    w = settings.MATCHING_WEIGHTS
    proximity = max(0.0, 1.0 - (distance_km / radius_km)) if radius_km else 0.0
    rating = float(artisan.avg_rating) / 5.0
    featured = 1.0 if artisan.is_featured else 0.0
    verified = 1.0 if (artisan.is_work_verified or artisan.user.is_identity_verified) else 0.0
    return round(
        w["proximity"] * proximity
        + w["rating"] * rating
        + w["featured"] * featured
        + w["verified"] * verified,
        4,
    )


def match_artisans(*, point, radius_km, category=None, q=None, limit=20):
    """Return a ranked list of (ArtisanProfile, distance_km, score)."""
    qs = ArtisanProfile.objects.filter(base_location__isnull=False, is_available=True)
    if category:
        qs = qs.filter(services__category_id=category)
    if q:
        qs = qs.filter(services__title__icontains=q)

    # Stage 1: within the customer's search radius (PostGIS, index-assisted).
    qs = (
        qs.filter(base_location__dwithin=(point, D(km=radius_km)))
        .distinct()
        .annotate(distance=Distance("base_location", point))
        .select_related("user")
    )

    # Stage 2: respect each artisan's own service radius, then score.
    results = []
    for artisan in qs:
        distance_km = artisan.distance.km
        if distance_km <= artisan.service_radius_km:
            results.append((artisan, distance_km, _score(artisan, distance_km, radius_km)))

    results.sort(key=lambda row: row[2], reverse=True)
    return results[:limit]
