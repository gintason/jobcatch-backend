from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from rest_framework.exceptions import ValidationError

from . import services
from .models import BillOrder, BillStatus, Wallet, WalletTransaction, GiftCardTrade


@admin.register(BillOrder)
class BillOrderAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "service", "provider", "customer_id", "variation_label",
                    "total", "payment_method", "status", "provider_status", "refund_button")
    list_filter = ("status", "service", "payment_method", "provider", "created_at")
    search_fields = ("request_id", "customer_id", "user__email", "provider_order_id",
                     "payment__reference")
    readonly_fields = ["refund_button", *[f.name for f in BillOrder._meta.fields]]
    actions = ("requery_selected",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    @admin.display(description="Refund")
    def refund_button(self, obj):
        if obj.pk is None or obj.status not in services.REFUNDABLE_BY_ADMIN:
            return "Refunded" if obj and obj.status == BillStatus.REFUNDED else "—"
        return format_html('<a class="button" style="background:#ba2121;color:#fff;padding:4px 10px;white-space:nowrap" '
                           'href="{}">Refund to wallet</a>', reverse("admin:billing_billorder_refund", args=[obj.pk]))

    def get_search_results(self, request, queryset, search_term):
        """Also find orders by the receipt number customers quote, e.g. JCB-20261115-F9F475C9."""
        qs, dup = super().get_search_results(request, queryset, search_term)
        term = search_term.strip().upper()
        if term.startswith("JCB-") and len(term.split("-")[-1]) == 8:
            qs = qs | queryset.filter(id__startswith=term.split("-")[-1].lower())
        return qs, dup

    def get_urls(self):
        return [path("<uuid:pk>/refund/", self.admin_site.admin_view(self.refund_view), name="billing_billorder_refund"),
                *super().get_urls()]

    def refund_view(self, request, pk):
        order = get_object_or_404(BillOrder.objects.select_related("user"), pk=pk)
        if not self.has_change_permission(request, order):
            messages.error(request, "You don't have permission to refund orders.")
            return redirect("admin:billing_billorder_change", pk)
        ctx = {**self.admin_site.each_context(request), "order": order, "title": "Refund to wallet",
               "opts": self.model._meta, "reason": "", "error": ""}
        if request.method == "POST":
            reason = request.POST.get("reason", "")
            try:
                services.admin_refund(order, admin=request.user, reason=reason)
            except ValidationError as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
                ctx.update(reason=reason, error=" ".join(str(v[0] if isinstance(v, list) else v) for v in detail.values()))
                return render(request, "admin/billing/billorder/refund.html", ctx)
            messages.success(request, f"Refunded ₦{order.total} to {order.user.email}'s wallet.")
            return redirect("admin:billing_billorder_change", pk)
        return render(request, "admin/billing/billorder/refund.html", ctx)

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


@admin.register(GiftCardTrade)
class GiftCardTradeAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "card_name", "card_currency", "card_amount",
                    "expected_payout", "user_payout", "status")
    list_filter = ("status", "card_type", "created_at")
    search_fields = ("request_id", "provider_reference", "user__email", "slug")
    readonly_fields = [f.name for f in GiftCardTrade._meta.fields if f.name != "code"]
    exclude = ("code",)            # redemption codes are never shown again after submission
    actions = ("requery_selected",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    @admin.action(description="↻ Check status with VTU.ng (credits the wallet if approved)")
    def requery_selected(self, request, queryset):
        for trade in queryset:
            services.requery_trade(trade)
        self.message_user(request, "Checked selected trades.", messages.SUCCESS)
