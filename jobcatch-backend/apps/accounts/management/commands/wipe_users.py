"""
Delete all non-staff registered users (customers, artisans, employers,
job seekers) and their associated data — for clearing out test/dummy accounts
before launch.

SAFE BY DEFAULT:
  * Dry run unless you pass --yes  (prints what WOULD be deleted, changes nothing)
  * Preserves staff/superusers unless you pass --include-staff
  * Runs inside a single transaction — if anything fails, NOTHING is deleted

Deletes in dependency order so PROTECT foreign keys (Payment.payer,
Booking.customer/artisan, Application.cv) don't block the cascade. Once the
users go, their profiles, services, jobs, CVs, reviews, subscriptions,
verifications, notifications, and messages cascade automatically.

Usage:
    python manage.py wipe_users                 # dry run — shows counts only
    python manage.py wipe_users --yes           # actually delete (keeps admins)
    python manage.py wipe_users --yes --include-staff   # delete EVERYONE
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Delete all non-staff users and their data (test-account cleanup)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually perform the deletion. Without this it's a dry run.",
        )
        parser.add_argument(
            "--include-staff",
            action="store_true",
            help="Also delete staff/superuser accounts (DANGEROUS).",
        )

    def _get_model(self, label):
        """Return a model by 'app_label.ModelName', or None if that app/model
        isn't installed — keeps the command resilient across app changes."""
        try:
            return apps.get_model(label)
        except Exception:
            return None

    def handle(self, *args, **options):
        do_delete = options["yes"]
        include_staff = options["include_staff"]

        # Which users are we targeting?
        target_users = User.objects.all()
        if not include_staff:
            target_users = target_users.filter(is_staff=False, is_superuser=False)

        user_ids = list(target_users.values_list("id", flat=True))
        total_users = len(user_ids)
        kept = User.objects.count() - total_users

        self.stdout.write(self.style.WARNING("\n=== JobCatch user wipe ==="))
        self.stdout.write(f"Users to delete : {total_users}")
        self.stdout.write(
            f"Users preserved : {kept} "
            f"({'staff/superusers' if not include_staff else 'none'})"
        )

        if total_users == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to delete. Done."))
            return

        # Dependency-ordered deletion. Each entry: (model label, queryset builder).
        # We only touch rows tied to the target users, so preserved admins keep
        # any data they own.
        def qs_or_empty(label, **filters):
            model = self._get_model(label)
            if model is None:
                return None
            return model.objects.filter(**filters)

        # Build the ordered delete plan.
        plan = []

        # 1. Payments (PROTECT on payer) — must go before users.
        plan.append(("payments.Payment", qs_or_empty("payments.Payment", payer_id__in=user_ids)))

        # 2. Bookings (PROTECT on customer & artisan profiles).
        #    A booking is tied to the target set if either side's user is targeted.
        booking_model = self._get_model("bookings.Booking")
        if booking_model is not None:
            plan.append((
                "bookings.Booking",
                booking_model.objects.filter(customer__user_id__in=user_ids)
                | booking_model.objects.filter(artisan__user_id__in=user_ids),
            ))

        # 3. Applications (PROTECT on cv) — must go before CVs cascade.
        plan.append(("jobs.Application", qs_or_empty("jobs.Application", seeker__user_id__in=user_ids)))

        # 4. Reviews authored by / about target users (defensive; usually cascades).
        review_model = self._get_model("reviews.Review")
        if review_model is not None:
            plan.append((
                "reviews.Review",
                review_model.objects.filter(author_id__in=user_ids)
                | review_model.objects.filter(target_id__in=user_ids),
            ))

        with transaction.atomic():
            for label, qs in plan:
                if qs is None:
                    self.stdout.write(f"  - {label}: (app not installed, skipped)")
                    continue
                count = qs.count()
                if do_delete and count:
                    qs.delete()
                self.stdout.write(
                    f"  - {label}: {count} "
                    f"{'deleted' if do_delete else 'would be deleted'}"
                )

            # Finally the users themselves — cascades profiles, services, jobs,
            # CVs, subscriptions, verifications, notifications, messages, etc.
            if do_delete:
                deleted_count, _ = User.objects.filter(id__in=user_ids).delete()
                self.stdout.write(
                    f"  - accounts.User (+cascades): {deleted_count} objects deleted"
                )
            else:
                self.stdout.write(
                    f"  - accounts.User (+cascades): {total_users} users would be deleted"
                )

            if not do_delete:
                # Dry run: roll back anything (there shouldn't be any changes anyway).
                transaction.set_rollback(True)

        if do_delete:
            self.stdout.write(self.style.SUCCESS("\n✓ Wipe complete."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "\nDRY RUN — nothing was deleted. Re-run with --yes to execute."
                )
            )
