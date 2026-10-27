from django.apps import AppConfig


class EngagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.engagement"
    verbose_name = "Upgrade prompts & job alerts"

    def ready(self):
        from . import signals  # noqa: F401
