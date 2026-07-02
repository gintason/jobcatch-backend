"""Keep artisan rating aggregates in sync whenever a review changes."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Review
from .services import recompute_artisan_rating


@receiver(post_save, sender=Review)
@receiver(post_delete, sender=Review)
def update_artisan_rating(sender, instance, **kwargs):
    recompute_artisan_rating(instance.target)
