"""Send a test OTP email — verifies SMTP config without registering a user."""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.tasks import send_otp_now


class Command(BaseCommand):
    help = "Send a test OTP email to verify SMTP settings."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Recipient address")

    def handle(self, *args, **options):
        email = options["email"]
        self.stdout.write(f"Backend : {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Host    : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        self.stdout.write(f"User    : {settings.EMAIL_HOST_USER}")
        self.stdout.write(f"TLS/SSL : TLS={settings.EMAIL_USE_TLS} SSL={settings.EMAIL_USE_SSL}")
        self.stdout.write(f"From    : {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"Sending to {email} ...")
        try:
            send_otp_now(email, "123456", "email_verify")
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"FAILED: {type(exc).__name__}: {exc}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Sent. Check the inbox (and spam folder)."))
