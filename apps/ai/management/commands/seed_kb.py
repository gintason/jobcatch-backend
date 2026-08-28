"""Seed the knowledge base with FAQ entries and embed them.

Usage: python manage.py seed_kb   (idempotent)

Embeddings are produced with the CURRENTLY configured provider. This matters:
mock embeddings are 64-dim and OpenAI embeddings are 1536-dim, and retrieval
compares them directly. If you switch LLM_PROVIDER, re-seed so the stored vectors
match the query-time provider — run `seed_kb` again after clearing KBChunk, or
change a chunk's title so this command re-embeds it.
"""
from django.core.management.base import BaseCommand

from apps.ai.models import KBChunk
from apps.ai.providers import get_provider

FAQ = [
    ("Booking a service",
     "To book an artisan, search by service and location, pick a service, choose a "
     "time, and pay. Your booking moves through pending, accepted, in progress, and completed."),
    ("Payments",
     "JobCatch supports card payments via Paystack. A 10% platform commission applies to "
     "each booking payment. You can view your transaction history in your account."),
    ("Subscriptions",
     "Artisans can subscribe to Premium (5,000 naira/month) or Pro (2,500 naira/month) plans "
     "for featured, boosted listings in search results. A subscription lasts 30 days."),
    ("Verification",
     "Artisans upload a government ID and job-sample videos; employers upload a CAC certificate. "
     "An admin reviews submissions and grants a verified badge on approval. Badges are earned, not bought."),
    ("Applying for jobs",
     "Job seekers upload a CV (PDF), search open jobs, and apply with an optional cover letter. "
     "Employers can shortlist, reject, or hire applicants."),
    ("Booking lifecycle",
     "A booking starts as 'pending'. The artisan can accept or cancel; the customer can also cancel. "
     "Once accepted, the artisan can start work ('in_progress'), then complete it. "
     "After completion, the customer can leave a review."),
    ("Application process",
     "Job seekers apply with a CV. Employers can shortlist, then hire, or reject. An applicant must be "
     "shortlisted before being hired. After a hire, the employer and seeker can start an in-app chat about the role."),
    ("Messaging",
     "In-app chat is available between the two parties of a booking (customer and artisan) or a job "
     "(employer and seeker). The conversation opens the first time either party starts a chat about that "
     "booking or job; it is not created automatically."),
    ("Verification badges",
     "The verified mark on an artisan means they submitted a government ID and a work video that an admin "
     "approved. Employers get a badge after their CAC certificate is approved."),
    ("Subscription benefits",
     "Premium and Pro subscribers appear higher in search results and are featured on the home page. "
     "This is visibility, not trust — it does not replace verification."),
    ("Payment security",
     "All card payments are handled by Paystack. JobCatch never stores your card number."),
    ("CV Service",
     "For a one-off 5,000 naira fee, a job seeker unlocks the concierge CV Service: they submit a CV (PDF) "
     "to the JobCatch admin, who reviews it and forwards selected CVs to employers. Employers see these under "
     "'Referred CVs' in their dashboard."),
    ("File uploads",
     "Allowed file types are JPG, PNG, PDF, and MP4. Images and documents are limited to 25 MB; "
     "artisan job videos (MP4) can be up to 100 MB."),
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
