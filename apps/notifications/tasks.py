"""Async email delivery for notifications."""
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task
def send_notification_email(email, title, body):
    send_mail(
        subject=title,
        message=body or title,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=True,
    )
