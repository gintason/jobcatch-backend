from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "payer", "purpose", "amount", "commission", "status", "gateway")
    list_filter = ("status", "gateway", "purpose")
    search_fields = ("reference",)
