"""Celery app. Import happens in config/__init__.py so tasks autodiscover."""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
app = Celery("jobcatch")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
