from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import Complaint


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    """Read the complaint here; reply from /admin/reviews so the complainant gets emailed."""

    list_display = ("reference", "created_at", "category", "full_name", "email", "phone",
                    "subject", "status", "handled_by")
    list_filter = ("status", "category", "created_at")
    search_fields = ("reference", "email", "phone", "full_name", "subject", "transaction_reference")
    date_hierarchy = "created_at"
    readonly_fields = ("reference", "created_at", "user", "full_name", "email", "phone", "category",
                       "subject", "description", "incident_date", "artisan", "artisan_name",
                       "transaction_reference", "attachment_link", "admin_response",
                       "responded_at", "resolved_at", "handled_by")
    exclude = ("attachment",)

    @admin.display(description="Attachment")
    def attachment_link(self, obj):
        if not obj.attachment:
            return "—"
        url = reverse("admin-complaint-attachment", args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noreferrer">Open &nearr;</a>', url)

    def has_add_permission(self, request):
        return False
