from celery import shared_task


@shared_task
def send_job_alerts_task(job_id):
    from apps.jobs.models import Job

    from .services import send_job_alerts

    job = Job.objects.filter(pk=job_id).select_related("employer__user", "category").first()
    if job:
        send_job_alerts(job)
