"""Fan out WhatsApp/in-app job alerts whenever a job goes live."""
import logging

from django.dispatch import receiver

from apps.jobs.signals import job_published

from .tasks import send_job_alerts_task

logger = logging.getLogger(__name__)


@receiver(job_published)
def on_job_published(sender, job, **kwargs):
    try:
        send_job_alerts_task.delay(str(job.id))
    except Exception:  # noqa: BLE001 — alerts must never break publishing
        logger.exception("Could not queue job alerts for %s", job.id)
