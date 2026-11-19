"""
Base settings for JobCatch. Environment-specific files import from here.
All secrets come from environment variables via django-environ.
"""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # .../backend

env = environ.Env(
    DEBUG=(bool, False),
    ACCESS_TOKEN_LIFETIME_MIN=(int, 15),
    REFRESH_TOKEN_LIFETIME_DAYS=(int, 14),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, []),
)
# Read .env if present (local dev); in production vars come from the platform.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ------------------------------------------------------------------ apps
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",  # GeoDjango / PostGIS
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",  # refresh rotation + blacklist
    "corsheaders",
    "drf_spectacular",
    "channels",
]

LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.bookings",
    "apps.payments",
    "apps.subscriptions",
    "apps.jobs",
    "apps.cvservice",
    "apps.reviews",
    "apps.messaging",
    "apps.notifications",
    "apps.verification",
    "apps.matching",
    "apps.ai",
    "apps.kyc",
    "apps.listings",
    "apps.billing",
    "apps.engagement",
    "apps.complaints",
    "apps.dispatch",
    "apps.referrals",
    "apps.transfers",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ------------------------------------------------------------------ middleware
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.payments.middleware.ClientChannelMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ------------------------------------------------------------------ database
# PostGIS backend is REQUIRED (models use gis.PointField).
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

# ------------------------------------------------------------------ auth
AUTH_USER_MODEL = "accounts.User"

# Argon2 first — strong, memory-hard hashing.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------------ DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Custom class enforces token_version (see accounts/authentication.py)
        "apps.accounts.authentication.VersionedJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        # Per-minute, not per-day: dashboards poll notifications every 30 s, so a
        # daily cap locked active users out after a few hours.
        "user": env("THROTTLE_USER_RATE", default="120/min"),
        "login": "10/min",     # scoped — brute-force protection
        "otp": "5/min",        # scoped — OTP request/verify abuse protection
        "complaint": "10/hour",  # scoped — complaint box spam protection
        "transfer_lookup": "30/min",  # scoped — recipient / account-name lookups
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "JobCatch API",
    "DESCRIPTION": "Home-services marketplace + job portal.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ------------------------------------------------------------------ JWT
# "Sign in with Google": OAuth client IDs whose ID tokens we accept (web now;
# add the Android / iOS client IDs here for the mobile app). Not secret.
# JobCatch's own OAuth clients (public IDs, not secrets): web, Android, iOS. The apps ask Google
# for tokens addressed to the web client, but all three are accepted. GOOGLE_CLIENT_IDS on Render
# adds to these (comma-separated; spaces are ignored).
_JOBCATCH_GOOGLE_CLIENTS = [
    "306763553702-jalc00es33k28etpj8shsufela5486t1.apps.googleusercontent.com",   # web
    "306763553702-q1os8sl8o30e412m51bi4a47uvkijnj3.apps.googleusercontent.com",   # Android
    "306763553702-5kvmuchb5s8ee3gjgpqb3mhnqeminfac.apps.googleusercontent.com",   # iOS
]
GOOGLE_CLIENT_IDS = list(dict.fromkeys(
    [c.strip() for c in env.list("GOOGLE_CLIENT_IDS", default=[]) if c.strip()] + _JOBCATCH_GOOGLE_CLIENTS))

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env("ACCESS_TOKEN_LIFETIME_MIN")),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env("REFRESH_TOKEN_LIFETIME_DAYS")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,   # old refresh dies on use → theft detection
    "UPDATE_LAST_LOGIN": True,
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ------------------------------------------------------------------ channels / celery
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [env("REDIS_URL", default="redis://localhost:6379/0")]},
    }
}
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False  # overridden to True in test settings

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/1"),
    }
}

# ------------------------------------------------------------------ email
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
# Port 587 -> STARTTLS; port 465 -> implicit SSL. Never both.
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=EMAIL_PORT == 465)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=not EMAIL_USE_SSL)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=20)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="JobCatch <info@jobcatchonline.com>")

# ------------------------------------------------------------------ CORS
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True  # needed for the httpOnly refresh cookie on web
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "x-jobcatch-client")

# ------------------------------------------------------------------ files
# JPG/PNG/PDF/MP4 only — enforced by validators in apps.common.validators
MAX_UPLOAD_SIZE_MB = 25
MAX_VIDEO_SIZE_MB = 100   # artisan job videos
ALLOWED_UPLOAD_EXTENSIONS = ["jpg", "jpeg", "png", "pdf", "mp4"]

# --- Payments & subscriptions ---
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", default="")
# Dashboard → Developers → API configuration → "Webhook signing secret" (needed with
# restricted API keys; without it webhooks are checked with PAYSTACK_SECRET_KEY).
PAYSTACK_WEBHOOK_SECRET = env("PAYSTACK_WEBHOOK_SECRET", default="")
PAYSTACK_BASE_URL = env("PAYSTACK_BASE_URL", default="")   # leave empty in production
FLUTTERWAVE_SECRET_KEY = env("FLUTTERWAVE_SECRET_KEY", default="")
COMMISSION_RATE = Decimal("0.10")           # 10% platform fee on booking payments
SUBSCRIPTION_PERIOD_DAYS = 30
SUBSCRIPTION_PRICES = {                       # Naira
    "premium": Decimal("5000.00"),            # top tier — most visibility
    "pro": Decimal("2500.00"),
}
CV_SERVICE_PRICE = 5000  # Naira, one-off concierge CV service
# Paying for an artisan booking through JobCatch means holding the artisan's money
# until it is paid out, which the payment providers' terms don't cover. Off by default:
# customers pay artisans directly.
BOOKING_PAYMENTS_ENABLED = env.bool("BOOKING_PAYMENTS_ENABLED", default=False)

PAYMENT_CALLBACK_URL = env(
    "PAYMENT_CALLBACK_URL", default="https://jobcatchonline.com/payments/callback"
)
# Public site, used for links in emails / WhatsApp alerts.
FRONTEND_URL = env("FRONTEND_URL", default="https://jobcatchonline.com").rstrip("/")
# Checkouts started from the mobile app (X-JobCatch-Client: mobile) return here;
# that page hands the user straight back to the app (jobcatch://payment-complete).
PAYMENT_APP_CALLBACK_URL = env(
    "PAYMENT_APP_CALLBACK_URL", default=f"{FRONTEND_URL}/payments/callback/app"
)

# Which gateway a checkout uses when the client doesn't ask for one.
DEFAULT_PAYMENT_GATEWAY = env("DEFAULT_PAYMENT_GATEWAY", default="paystack")
# Bill payments and wallet top-ups always use this gateway, whatever the
# client asks for. Everything else (plans, listings, fees) uses the default.
BILLING_GATEWAY = env("BILLING_GATEWAY", default="flutterwave")
# Flutterwave signs webhooks by echoing this "secret hash" (set in the FLW dashboard).
FLUTTERWAVE_WEBHOOK_HASH = env("FLUTTERWAVE_WEBHOOK_HASH", default="")

# Support inbox that receives a copy of every complaint (blank = in-app alerts only).
COMPLAINTS_NOTIFY_EMAIL = env("COMPLAINTS_NOTIFY_EMAIL", default="info@jobcatchonline.com")

# --- Paid job listings ---
# When True, a job only goes live once a listing credit (bought package) is spent.
PAID_JOB_LISTINGS_ENABLED = env.bool("PAID_JOB_LISTINGS_ENABLED", default=True)

# --- Employer KYC ---
# Unverified employers cannot post jobs while this is True.
KYC_REQUIRED_TO_POST_JOBS = env.bool("KYC_REQUIRED_TO_POST_JOBS", default=True)
# Auto-verify when every automated check passes; otherwise an admin decides.
KYC_AUTO_APPROVE = env.bool("KYC_AUTO_APPROVE", default=False)
# "dojah" for live CAC/TIN/NIN lookups; "manual" skips lookups (admin reviews docs).
KYC_PROVIDER = env("KYC_PROVIDER", default="manual")
DOJAH_BASE_URL = env("DOJAH_BASE_URL", default="https://api.dojah.io")
DOJAH_APP_ID = env("DOJAH_APP_ID", default="")
DOJAH_SECRET_KEY = env("DOJAH_SECRET_KEY", default="")
# Paths are configurable so a provider API revision doesn't need a code deploy.
DOJAH_CAC_PATH = env("DOJAH_CAC_PATH", default="/api/v1/kyc/cac/basic")
DOJAH_TIN_PATH = env("DOJAH_TIN_PATH", default="/api/v1/kyc/tin")
DOJAH_NIN_PATH = env("DOJAH_NIN_PATH", default="/api/v1/kyc/nin")
KYC_OTP_TTL_MINUTES = 10

# --- SMS (phone OTP) ---
# "termii" sends real SMS; "console" logs the code (development only).
SMS_BACKEND = env("SMS_BACKEND", default="console")
TERMII_API_KEY = env("TERMII_API_KEY", default="")
TERMII_SENDER_ID = env("TERMII_SENDER_ID", default="JobCatch")
TERMII_BASE_URL = env("TERMII_BASE_URL", default="https://api.ng.termii.com")
TERMII_CHANNEL = env("TERMII_CHANNEL", default="dnd")  # dnd reaches DND numbers

# --- Artisan verification + upgrades ---
ARTISAN_VERIFICATION_FEE = Decimal(env("ARTISAN_VERIFICATION_FEE", default="5000"))
# One-off fee an employer pays when submitting business verification (KYC).
# Resubmitting after a rejection is free. Set to 0 to make verification free.
EMPLOYER_VERIFICATION_FEE = Decimal(env("EMPLOYER_VERIFICATION_FEE", default="8000"))

# --- Job seeker Pro plan ---
SEEKER_PRO_PRICE = Decimal(env("SEEKER_PRO_PRICE", default="3000"))
SEEKER_PRO_PERIOD_DAYS = env.int("SEEKER_PRO_PERIOD_DAYS", default=30)

# --- WhatsApp job alerts (Meta WhatsApp Cloud API) ---
# "cloud_api" sends; "console" logs. Business-initiated messages must use an
# approved template whose body takes {{1}} title, {{2}} company, {{3}} link.
WHATSAPP_BACKEND = env("WHATSAPP_BACKEND", default="console")
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_JOB_ALERT_TEMPLATE = env("WHATSAPP_JOB_ALERT_TEMPLATE", default="job_alert")
WHATSAPP_TEMPLATE_LANG = env("WHATSAPP_TEMPLATE_LANG", default="en")

# --- Bill payments (VTU.ng Partner API v1) ---
# Live https://api.vtu.ng/v1 with a vtu_sk_live_ key; sandbox https://sandbox.vtu.ng/v1
# with a vtu_sk_test_ key. Keys are environment-locked.
VTU_BASE_URL = env("VTU_BASE_URL", default="https://api.vtu.ng/v1")
VTU_API_KEY = env("VTU_API_KEY", default="")
# Secret path segment of the webhook URL: /api/v1/billing/webhook/vtu/<token>/
VTU_WEBHOOK_TOKEN = env("VTU_WEBHOOK_TOKEN", default="")
BILL_SERVICE_FEES = {
    "airtime": Decimal(env("BILL_FEE_AIRTIME", default="0")),
    "data": Decimal(env("BILL_FEE_DATA", default="0")),
    "electricity": Decimal(env("BILL_FEE_ELECTRICITY", default="0")),
    "tv": Decimal(env("BILL_FEE_TV", default="0")),
    "epins": Decimal(env("BILL_FEE_EPINS", default="0")),
    "betting": Decimal(env("BILL_FEE_BETTING", default="0")),
    "education": Decimal(env("BILL_FEE_EDUCATION", default="0")),
    "giftcard": Decimal(env("BILL_FEE_GIFTCARD", default="0")),
    "rmb": Decimal(env("BILL_FEE_RMB", default="0")),
}
# Betting-wallet funding can be switched off without a deploy (e.g. if a
# payment provider's terms don't allow it).
BILL_BETTING_ENABLED = env.bool("BILL_BETTING_ENABLED", default=True)
BILL_EDUCATION_ENABLED = env.bool("BILL_EDUCATION_ENABLED", default=True)
# Gift cards and RMB need VTU.ng business verification (KYB) and are money-
# transfer / FX style services: keep them OFF until KYB is approved and your
# payment providers have confirmed in writing that they allow them.
BILL_GIFTCARD_BUY_ENABLED = env.bool("BILL_GIFTCARD_BUY_ENABLED", default=False)
BILL_GIFTCARD_SELL_ENABLED = env.bool("BILL_GIFTCARD_SELL_ENABLED", default=False)
BILL_RMB_ENABLED = env.bool("BILL_RMB_ENABLED", default=False)
# JobCatch's margin, in percent, on top of VTU.ng's price (buying) or kept from
# VTU.ng's payout (selling gift cards).
GIFTCARD_BUY_MARGIN_PERCENT = Decimal(env("GIFTCARD_BUY_MARGIN_PERCENT", default="2"))
GIFTCARD_SELL_MARGIN_PERCENT = Decimal(env("GIFTCARD_SELL_MARGIN_PERCENT", default="5"))
RMB_MARGIN_PERCENT = Decimal(env("RMB_MARGIN_PERCENT", default="2"))
# --- Dispatch & delivery ---
# Delivery fees are paid in the app at pickup and split by Paystack: the rider's
# share settles to their bank, the rest to JobCatch. Only Paystack does splits.
DISPATCH_GATEWAY = "paystack"
DISPATCH_RIDER_SHARE_PERCENT = Decimal(env("DISPATCH_RIDER_SHARE_PERCENT", default="80"))
DISPATCH_MATCH_RADIUS_KM = int(env("DISPATCH_MATCH_RADIUS_KM", default="10"))
DISPATCH_ROAD_FACTOR = Decimal(env("DISPATCH_ROAD_FACTOR", default="1.3"))   # straight line -> road
DISPATCH_REQUEST_TTL_MINUTES = int(env("DISPATCH_REQUEST_TTL_MINUTES", default="45"))
DISPATCH_MAX_DISTANCE_KM = int(env("DISPATCH_MAX_DISTANCE_KM", default="80"))

WALLET_MIN_TOPUP = Decimal("100")
WALLET_MAX_TOPUP = Decimal("500000")

# --- Geo-matching engine ---
MATCHING_DEFAULT_RADIUS_KM = 25
MATCHING_WEIGHTS = {          # must sum to 1.0; tune without code changes
    "proximity": 0.40,
    "rating": 0.30,
    "featured": 0.20,         # subscription-driven visibility boost
    "verified": 0.10,
}

# --- AI chatbot (RAG + tool-calling) ---
# Provider defaults to "mock" (offline, no key) until a real key is configured.
LLM_PROVIDER = env("LLM_PROVIDER", default="mock")
LLM_API_KEY = env("LLM_API_KEY", default="")
OPENAI_CHAT_MODEL = env("OPENAI_CHAT_MODEL", default="gpt-4o-mini")
OPENAI_EMBED_MODEL = env("OPENAI_EMBED_MODEL", default="text-embedding-3-small")
AI_MAX_TOOL_ROUNDS = 3
AI_KB_TOP_K = 3

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
# KYC / verification documents. Never served by the public /media/ route —
# only through permission-checked download endpoints (apps.common.storage).
PRIVATE_MEDIA_ROOT = env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media"))

# App-specific knobs
OTP_TTL_MINUTES = 10
OTP_LENGTH = 6

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}


# ------------------------------------------------------------------ referrals
# The inviter earns REFERRAL_REWARD (₦) in their wallet once the invitee makes a
# first purchase (a bill of at least REFERRAL_MIN_SPEND, or a completed delivery).
REFERRAL_ENABLED = env.bool("REFERRAL_ENABLED", default=True)
REFERRAL_REWARD = env("REFERRAL_REWARD", default="500")
REFERRAL_NEW_USER_BONUS = env("REFERRAL_NEW_USER_BONUS", default="0")
REFERRAL_MIN_SPEND = env("REFERRAL_MIN_SPEND", default="1000")
REFERRAL_MONTHLY_CAP = env.int("REFERRAL_MONTHLY_CAP", default=20)
REFERRAL_APPLY_DAYS = env.int("REFERRAL_APPLY_DAYS", default=7)


# ------------------------------------------------------------------ transfers
# Wallet → wallet (instant, free) and wallet → bank (Paystack Transfers; enable
# Transfers and turn OFF "transfer OTP" in the Paystack dashboard).
WALLET_TRANSFER_ENABLED = env.bool("WALLET_TRANSFER_ENABLED", default=True)
BANK_TRANSFER_ENABLED = env.bool("BANK_TRANSFER_ENABLED", default=True)
BANK_TRANSFER_FEE = env("BANK_TRANSFER_FEE", default="50")
TRANSFER_MIN = env("TRANSFER_MIN", default="100")
TRANSFER_MAX_SINGLE = env("TRANSFER_MAX_SINGLE", default="500000")
TRANSFER_DAILY_LIMIT = env("TRANSFER_DAILY_LIMIT", default="1000000")
# Card top-ups can be spent on bills at once but transferred out only after this many hours.
TRANSFER_TOPUP_HOLD_HOURS = env.int("TRANSFER_TOPUP_HOLD_HOURS", default=24)
