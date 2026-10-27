from django.contrib import admin, messages

from .gateways import GatewayError
from .models import Payment, PaymentEvent, PaymentStatus
from .services import verify_with_gateway


class PaymentEventInline(admin.TabularInline):
    model = PaymentEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "kind", "source", "note")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "reference", "payer", "purpose", "amount",
                    "status", "gateway", "paid_at", "fulfilled_at", "created_at")
    list_filter = ("status", "gateway", "purpose", "created_at")
    search_fields = ("reference", "invoice_number", "payer__email", "description")
    readonly_fields = ("payer", "purpose", "booking", "gateway", "reference", "invoice_number",
                       "amount", "commission", "currency", "description", "metadata",
                       "raw_response", "paid_at", "fulfilled_at", "failure_reason",
                       "created_at", "updated_at")
    fields = readonly_fields + ("status",)
    inlines = (PaymentEventInline,)
    actions = ("reverify_with_gateway",)
    date_hierarchy = "created_at"

    @admin.action(description="Re-verify with gateway (and fulfil if paid)")
    def reverify_with_gateway(self, request, queryset):
        done = 0
        for payment in queryset.exclude(status=PaymentStatus.REFUNDED):
            try:
                verify_with_gateway(payment, source=f"admin:{request.user.email}")
                done += 1
            except GatewayError as exc:
                self.message_user(request, f"{payment.reference}: {exc}", messages.WARNING)
        self.message_user(request, f"Re-verified {done} payment(s).", messages.SUCCESS)
