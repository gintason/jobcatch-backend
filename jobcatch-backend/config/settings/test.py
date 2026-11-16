"""Test settings — fast, isolated, synchronous."""
from .base import *  # noqa

DEBUG = False
CELERY_TASK_ALWAYS_EAGER = True          # run tasks inline
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
