from django.contrib import admin

from . import services
from .models import BankTransfer, BankTransferStatus, WalletTransfer


@admin.register(BankTransfer)
class BankTransferAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "amount", "fee", "account_name", "bank_name", "status", "created_at")
    list_filter = ("status", "bank_name")
    search_fields = ("reference", "user__email", "account_number", "account_name")
    readonly_fields = [f.name for f in BankTransfer._meta.fields]
    actions = ["recheck"]

    @admin.action(description="Re-check status with Paystack")
    def recheck(self, request, queryset):
        for bt in queryset.filter(status=BankTransferStatus.PROCESSING):
            services.requery(bt)
        self.message_user(request, "Re-checked processing transfers.")


@admin.register(WalletTransfer)
class WalletTransferAdmin(admin.ModelAdmin):
    list_display = ("reference", "sender", "recipient", "amount", "created_at")
    search_fields = ("reference", "sender__email", "recipient__email")
    readonly_fields = [f.name for f in WalletTransfer._meta.fields]
