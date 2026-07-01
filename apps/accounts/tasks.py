"""Async email delivery for OTP codes (never block the request thread)."""
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task
def send_otp_email(email, code, purpose):
    subject = {
        "email_verify": "Verify your JobCatch email",
        "password_reset": "Reset your JobCatch password",
    }.get(purpose, "Your JobCatch code")
    body = (
        f"Your JobCatch verification code is: {code}\n\n"
        f"It expires in {settings.OTP_TTL_MINUTES} minutes. "
        f"If you didn't request this, you can ignore this email."
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email])
