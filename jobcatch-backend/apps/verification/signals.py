"""
Grant the verified badge whenever a Verification is approved — no matter how.

The badge logic previously lived only in the API view, so an admin approving a
document from Django admin changed the status but never granted the badge. A
signal makes approval mean the same thing everywhere.

apply_verification_badge() is idempotent, so double-calling (API + signal) is safe.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Verification, VerificationStatus
from .services import apply_verification_badge


@receiver(post_save, sender=Verification)
def grant_badge_on_approval(sender, instance, **kwargs):
    if instance.status == VerificationStatus.APPROVED:
        apply_verification_badge(instance)
