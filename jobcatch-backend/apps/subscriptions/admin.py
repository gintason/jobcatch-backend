from django.contrib import admin
from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "is_active", "started_at", "expires_at", "auto_renew")
    list_filter = ("plan", "is_active")
