"""Domain signals for the job portal (decouples job alerts from listing logic)."""
from django.dispatch import Signal

# Sent when a job becomes visible to seekers. kwargs: job
job_published = Signal()
