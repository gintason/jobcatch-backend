"""
Periodic housekeeping — run every 15 minutes from a Render Cron Job:

    python manage.py run_scheduled_tasks

Every step is idempotent and isolated: one failing step is logged and the rest
still run. Expiry is also enforced at query time, so a late run never shows a
stale job listing; this command handles the side effects (closing, notifying,
refunding, reconciling).
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


def _steps():
    from apps.billing.services import requery_stale_orders
    from apps.engagement.services import send_upgrade_nudges
    from apps.listings.services import expire_listings
    from apps.payments.services import reconcile_payments
    from apps.subscriptions.services import expire_subscriptions, send_renewal_reminders

    return [
        ("reconcile_payments", reconcile_payments),
        ("requery_bill_orders", requery_stale_orders),
        ("expire_job_listings", expire_listings),
        ("expire_subscriptions", expire_subscriptions),
        ("renewal_reminders", send_renewal_reminders),
        ("upgrade_nudges", send_upgrade_nudges),
    ]


class Command(BaseCommand):
    help = "Run periodic housekeeping (expiry, reconciliation, requery, reminders, nudges)."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="*", help="Run only these steps.")
        parser.add_argument("--skip-nudges", action="store_true",
                            help="Skip upgrade nudges (e.g. on the 15-minute schedule).")

    def handle(self, *args, only=None, skip_nudges=False, **options):
        started = timezone.now()
        failures = 0
        for name, fn in _steps():
            if only and name not in only:
                continue
            if skip_nudges and name == "upgrade_nudges":
                continue
            try:
                result = fn()
                self.stdout.write(f"{name}: {result}")
            except Exception:  # noqa: BLE001
                failures += 1
                logger.exception("Scheduled step %s failed", name)
                self.stderr.write(f"{name}: FAILED (see logs)")
        took = (timezone.now() - started).total_seconds()
        self.stdout.write(f"done in {took:.1f}s, {failures} failure(s)")
