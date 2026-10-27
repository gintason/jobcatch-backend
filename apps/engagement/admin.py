from django.contrib import admin
from django.db.models import Count, Q

from .models import JobAlertDelivery, JobAlertSubscription, UpgradePrompt


@admin.register(UpgradePrompt)
class UpgradePromptAdmin(admin.ModelAdmin):
    """Edit prompt copy, CTAs and frequency without a deploy. Shows conversion counts."""

    list_display = ("key", "audience", "trigger", "style", "title", "priority", "is_active",
                    "shown", "clicked", "dismissed")
    list_editable = ("priority", "is_active")
    list_filter = ("audience", "style", "is_active")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _shown=Count("states", filter=Q(states__shown_count__gt=0)),
            _clicked=Count("states", filter=Q(states__clicked_at__isnull=False)),
            _dismissed=Count("states", filter=Q(states__dismissed_at__isnull=False)),
        )

    @admin.display(description="Users shown", ordering="_shown")
    def shown(self, obj):
        return obj._shown

    @admin.display(description="Clicked CTA", ordering="_clicked")
    def clicked(self, obj):
        return obj._clicked

    @admin.display(description="Dismissed", ordering="_dismissed")
    def dismissed(self, obj):
        return obj._dismissed


@admin.register(JobAlertSubscription)
class JobAlertSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "whatsapp_number", "keywords", "is_active", "last_sent_at")
    search_fields = ("user__email", "whatsapp_number")
    filter_horizontal = ("categories",)


@admin.register(JobAlertDelivery)
class JobAlertDeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "subscription", "job", "status", "error")
    list_filter = ("status",)
