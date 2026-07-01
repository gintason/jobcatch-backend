"""Production settings — hardened for Render deployment."""
from .base import *  # noqa

DEBUG = False

# --- Transport security ---
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render terminates TLS
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Cookies (the refresh-token cookie must be secure) ---
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# --- Email over SMTP in production ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# --- Static via WhiteNoise (add to MIDDLEWARE right after SecurityMiddleware) ---
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# --- Media/verification docs → private S3 bucket, served via signed URLs ---
DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")          # noqa
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")  # noqa
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")  # noqa
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default="")      # noqa
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True   # signed URLs for private objects
