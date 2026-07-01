from django.contrib import admin
from .models import Verification


@admin.register(Verification)
class VerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "status", "reviewed_by", "created_at")
    list_filter = ("type", "status")
