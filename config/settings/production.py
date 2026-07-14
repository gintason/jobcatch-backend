"""Production settings — hardened for Render deployment."""
from .base import *  # noqa

DEBUG = False

# Render's DATABASE_URL uses the postgres:// scheme; GeoDjango needs the spatial
# backend, so force it rather than relying on the URL prefix being right.
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"  # noqa

# --- Transport security ---
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render terminates TLS
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Cookies ---
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])  # noqa

# --- Email over real SMTP ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# --- Celery ---
# We deploy no Celery worker, but Redis IS reachable — so .delay() would succeed,
# push the task onto a queue nobody consumes, and the OTP would silently vanish.
# Running tasks eagerly (inline, in the request) keeps registration working.
# Set CELERY_TASK_ALWAYS_EAGER=False once a real worker service exists.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)  # noqa
CELERY_TASK_EAGER_PROPAGATES = True

# --- Static files via WhiteNoise (no nginx needed) ---
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Media (uploads: portfolio photos, CVs, verification docs) ---
# Prefer S3-compatible object storage. Render's filesystem is EPHEMERAL: without
# a bucket (or a mounted persistent disk), uploaded files vanish on every deploy.
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")  # noqa
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")              # noqa
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")      # noqa
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default="")  # noqa
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="")    # noqa
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True   # signed URLs — verification docs stay private
    AWS_S3_FILE_OVERWRITE = False
    STORAGES["default"] = {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"}
    SERVE_MEDIA = False          # S3 serves the files directly
else:
    # No object storage: uploads live on a Render persistent disk, and Django
    # serves them itself. RENDER_DISK_PATH is the disk's mount point — without a
    # disk mounted there, every deploy wipes the files.
    MEDIA_ROOT = env("RENDER_DISK_PATH", default=str(BASE_DIR / "media"))  # noqa
    SERVE_MEDIA = True

# --- Logging: surface errors in Render's log stream ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{levelname} {name} {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
