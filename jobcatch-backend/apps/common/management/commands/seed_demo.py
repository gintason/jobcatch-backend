"""
Seed baseline data for local dev / demos.

Usage:  python manage.py seed_demo
Creates service categories and one verified demo user per role
(password: Demo!Pass123). Idempotent — safe to re-run.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import UserRole
from apps.catalog.models import Category

CATEGORIES = [
    "Plumbing", "Electrical", "Carpentry", "Cleaning", "Painting",
    "AC Repair", "Catering", "Tailoring", "Hairdressing", "IT Support",
]

DEMO_USERS = [
    ("customer@jobcatch.test", "Chloe Customer", UserRole.CUSTOMER),
    ("artisan@jobcatch.test", "Ada Artisan", UserRole.ARTISAN),
    ("employer@jobcatch.test", "Emeka Employer", UserRole.EMPLOYER),
    ("seeker@jobcatch.test", "Sade Seeker", UserRole.JOB_SEEKER),
]

DEMO_PASSWORD = "Demo!Pass123"


class Command(BaseCommand):
    help = "Seed categories and demo users (one per role)."

    @transaction.atomic
    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        created_cats = 0
        for name in CATEGORIES:
            _, created = Category.objects.get_or_create(name=name)
            created_cats += int(created)
        self.stdout.write(self.style.SUCCESS(f"Categories: +{created_cats} (total {Category.objects.count()})"))

        for email, name, role in DEMO_USERS:
            if User.objects.filter(email=email).exists():
                continue
            user = User.objects.create_user(
                email=email, password=DEMO_PASSWORD, full_name=name,
                role=role, is_email_verified=True,
            )
            self.stdout.write(self.style.SUCCESS(f"User: {email} ({role})"))

        # Admin (staff) demo account.
        if not User.objects.filter(email="admin@jobcatch.test").exists():
            User.objects.create_superuser(
                email="admin@jobcatch.test", password=DEMO_PASSWORD, full_name="Site Admin",
            )
            self.stdout.write(self.style.SUCCESS("Superuser: admin@jobcatch.test"))

        self.stdout.write(self.style.WARNING(f"\nAll demo passwords: {DEMO_PASSWORD}"))
