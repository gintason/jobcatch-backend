from django.contrib import admin, messages

from . import services
from .models import BillOrder, BillStatus, Wallet, WalletTransaction


@admin.register(BillOrder)
class BillOrderAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "service", "provider", "customer_id", "variation_label",
                    "total", "payment_method", "status", "provider_status")
    list_filter = ("status", "service", "payment_method", "provider", "created_at")
    search_fields = ("request_id", "customer_id", "user__email", "provider_order_id",
                     "payment__reference")
    readonly_fields = [f.name for f in BillOrder._meta.fields]
    actions = ("requery_selected",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    @admin.action(description="↻ Requery status with VTU.ng (refunds if the biller failed it)")
    def requery_selected(self, request, queryset):
        for order in queryset.filter(status__in=[BillStatus.PROCESSING, BillStatus.QUEUED]):
            services.requery_order(order)
        self.message_user(request, "Requeried processing orders.", messages.SUCCESS)


class WalletTransactionInline(admin.TabularInline):
    model = WalletTransaction
    extra = 0
    can_delete = False
    fields = ("created_at", "kind", "source", "amount", "balance_after", "reference", "description")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "balance")
    inlines = (WalletTransactionInline,)

    def has_add_permission(self, request):
        return False


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "wallet", "kind", "source", "amount", "balance_after", "reference")
    list_filter = ("kind", "source")
    search_fields = ("reference", "wallet__user__email")
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
