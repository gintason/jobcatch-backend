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

# macOS: GeoDjango can't auto-discover GDAL/GEOS when they live inside
# Postgres.app rather than a standard system path, so point at them explicitly.
# Only set when provided — on Linux (Docker, Render) the libraries are found
# automatically and these stay unset.
_gdal = env("GDAL_LIBRARY_PATH", default="")
_geos = env("GEOS_LIBRARY_PATH", default="")
if _gdal:
    GDAL_LIBRARY_PATH = _gdal
if _geos:
    GEOS_LIBRARY_PATH = _geos

# Serve static files (Django admin CSS) under uvicorn, which — unlike runserver —
# has no built-in static handler.
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
WHITENOISE_USE_FINDERS = True
