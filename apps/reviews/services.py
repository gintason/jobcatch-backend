"""Recompute an artisan's denormalized rating aggregates from their reviews."""
from django.db.models import Avg, Count


def recompute_artisan_rating(artisan_user):
    from apps.accounts.models import ArtisanProfile

    from .models import Review

    profile = ArtisanProfile.objects.filter(user=artisan_user).first()
    if not profile:
        return
    agg = Review.objects.filter(target=artisan_user, is_visible=True).aggregate(
        avg=Avg("rating"), count=Count("id")
    )
    avg = agg["avg"] or 0
    count = agg["count"] or 0
    profile.avg_rating = round(avg, 2)
    profile.rating_count = count
    # Simple bounded 0-100 reputation from average stars; tune / blend volume later.
    profile.reputation_score = int(round(avg * 20))
    profile.save(update_fields=[
        "avg_rating", "rating_count", "reputation_score", "updated_at"
    ])
