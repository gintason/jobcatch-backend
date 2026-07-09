"""Local development settings."""

from .base import *  # noqa

DEBUG = True

# See OTP codes / emails printed to the console in dev.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Run Celery tasks inline (no separate worker needed just to see OTP codes in dev).
CELERY_TASK_ALWAYS_EAGER = True

# Dev CORS: allow any origin so the frontend Codespace can call the API.
# (Not for production — lock this down before deploy.)
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = False

INTERNAL_IPS = ["127.0.0.1"]
