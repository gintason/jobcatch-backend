from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity", "entity_id", "actor", "created_at")
    list_filter = ("action", "entity")
    search_fields = ("entity_id", "action")
    readonly_fields = ("actor", "action", "entity", "entity_id", "metadata", "ip_address", "created_at")
