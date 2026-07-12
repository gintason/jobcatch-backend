"""Local development settings."""
from .base import *  # noqa

DEBUG = True

# See OTP codes / emails printed to the console in dev.
# Real SMTP as soon as EMAIL_HOST is configured; console otherwise so dev
# never silently depends on a mail server.
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)

# Run Celery tasks inline (no separate worker needed just to see OTP codes in dev).
CELERY_TASK_ALWAYS_EAGER = True

# Relax CORS for local clients if not explicitly set.
if not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:5173",    # Vite web
        "http://localhost:19006",   # Expo web
    ]

INTERNAL_IPS = ["127.0.0.1"]
