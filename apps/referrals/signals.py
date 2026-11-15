"""Pay referral rewards when an invitee's purchase completes."""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _later(user_id):
    def run():
        from apps.accounts.models import User

        from .services import check_qualification
        try:
            user = User.objects.filter(pk=user_id).first()
            if user:
                check_qualification(user)
        except Exception:  # noqa: BLE001 — a reward problem must never break a purchase
            logger.exception("Referral check failed for %s", user_id)
    transaction.on_commit(run)


@receiver(post_save, sender="billing.BillOrder")
def bill_done(sender, instance, **kwargs):
    if instance.status == "successful":
        _later(instance.user_id)


@receiver(post_save, sender="dispatch.Delivery")
def delivery_done(sender, instance, **kwargs):
    if instance.status == "delivered":
        _later(instance.customer_id)
