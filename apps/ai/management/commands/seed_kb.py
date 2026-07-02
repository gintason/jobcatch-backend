"""Seed the knowledge base with FAQ entries and embed them.

Usage: python manage.py seed_kb   (idempotent)
Uses the configured provider for embeddings (mock offline by default).
"""
from django.core.management.base import BaseCommand

from apps.ai.models import KBChunk
from apps.ai.providers import get_provider

FAQ = [
    ("Booking a service",
     "To book an artisan, search by service and location, pick a service, choose a "
     "time, and pay. Your booking moves through pending, accepted, in progress, and completed."),
    ("Payments",
     "JobCatch supports card payments via Paystack. A platform commission applies to "
     "each booking. You can view your transaction history in your account."),
    ("Subscriptions",
     "Artisans can subscribe to Premium or Pro plans for featured, boosted listings "
     "in search results."),
    ("Verification",
     "Artisans upload ID and job-sample videos; employers upload CAC documents. An "
     "admin reviews submissions and grants a verified badge on approval."),
    ("Applying for jobs",
     "Job seekers upload a CV (and NYSC certificate if a graduate), search open jobs, "
     "and apply. Employers can shortlist, reject, or hire applicants."),
]


class Command(BaseCommand):
    help = "Seed the AI knowledge base with FAQ chunks."

    def handle(self, *args, **options):
        provider = get_provider()
        created = 0
        for title, content in FAQ:
            if KBChunk.objects.filter(title=title).exists():
                continue
            embedding = provider.embed([content])[0]
            KBChunk.objects.create(title=title, content=content, embedding=embedding)
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f"KB seeded: +{created} (total {KBChunk.objects.count()})"))
