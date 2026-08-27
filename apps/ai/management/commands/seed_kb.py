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
      ("Booking lifecycle", "A booking starts as 'pending'. The artisan can accept or cancel; the customer can also cancel. Once accepted, it moves to 'accepted', then the artisan can start work ('in_progress'), then complete it. After completion, the customer can leave a review."),
    ("Application process", "Job seekers apply with a CV. Employers can shortlist, reject, or hire. Once hired, a chat is opened."),
    ("Verification badges", "The blue checkmark on artisans means they have submitted a government ID and a work video, approved by admin. Employers get a badge after uploading a CAC certificate."),
    ("Subscription benefits", "Premium and Pro subscribers appear higher in search results and are featured on the home page. This is visibility, not trust."),
    ("Payment security", "All payments are handled by Paystack. JobCatch never stores your card number."),
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
