"""
Admin review queue for verification documents.

This is the human step behind the blue "Verified" badge: an admin opens the
submitted document, decides, and the badge propagates via the approval signal.
Without this screen, no one could ever be verified.
"""
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from apps.common.audit import history

from .models import (
    ArtisanVerificationApplication,
    ArtisanVerificationDocument,
    ArtisanVerificationStatus,
    Verification,
    VerificationStatus,
)
from .services import review_application


@admin.register(Verification)
class VerificationAdmin(admin.ModelAdmin):
    list_display = (
        "user_email", "type", "status_badge", "document_link",
        "reviewed_by", "created_at",
    )
    list_filter = ("status", "type", "created_at")
    search_fields = ("user__email", "user__full_name")
    readonly_fields = ("user", "type", "document", "document_link", "created_at", "reviewed_by")
    fields = ("user", "type", "document_link", "status", "review_note", "reviewed_by", "created_at")
    actions = ("approve_selected", "reject_selected")
    date_hierarchy = "created_at"
    ordering = ("status", "-created_at")   # pending first

    @admin.display(description="User", ordering="user__email")
    def user_email(self, obj):
        return f"{obj.user.full_name or '—'} ({obj.user.email})"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colours = {"pending": "#b7791f", "approved": "#2f855a", "rejected": "#9b2c2c"}
        return format_html(
            '<b style="color:{}">{}</b>',
            colours.get(obj.status, "#444"),
            obj.get_status_display(),
        )

    @admin.display(description="Document")
    def document_link(self, obj):
        if not obj.document:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noreferrer">Open document &nearr;</a>',
            obj.document.url,
        )

    def _review(self, request, queryset, new_status):
        """Set status + reviewer. Saved one-by-one so the badge signal fires."""
        changed = 0
        for verification in queryset:
            if verification.status == new_status:
                continue
            verification.status = new_status
            verification.reviewed_by = request.user
            verification.save(update_fields=["status", "reviewed_by", "updated_at"])
            changed += 1
        return changed

    @admin.action(description="✓ Approve selected (grants the verified badge)")
    def approve_selected(self, request, queryset):
        n = self._review(request, queryset, VerificationStatus.APPROVED)
        self.message_user(request, f"Approved {n} verification(s).", messages.SUCCESS)

    @admin.action(description="✗ Reject selected")
    def reject_selected(self, request, queryset):
        n = self._review(request, queryset, VerificationStatus.REJECTED)
        self.message_user(request, f"Rejected {n} verification(s).", messages.WARNING)

    def save_model(self, request, obj, form, change):
        """Stamp the reviewer when an admin edits a single record."""
        if change and "status" in form.changed_data:
            obj.reviewed_by = request.user
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------------- paid artisan verification
class ArtisanVerificationDocumentInline(admin.TabularInline):
    model = ArtisanVerificationDocument
    extra = 0
    can_delete = False
    fields = ("doc_type", "open_link", "original_name", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def open_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noreferrer">Open &nearr;</a>',
                           reverse("artisan-document-download", args=[obj.pk]))


@admin.register(ArtisanVerificationApplication)
class ArtisanVerificationApplicationAdmin(admin.ModelAdmin):
    list_display = ("artisan", "status", "fee_paid_at", "years_experience", "id_type",
                    "submitted_at", "reviewed_by")
    list_filter = ("status", "id_type", "submitted_at")
    search_fields = ("artisan__user__email", "artisan__user__full_name", "id_number")
    ordering = ("status", "submitted_at")
    inlines = (ArtisanVerificationDocumentInline,)
    readonly_fields = ("artisan", "status", "categories", "years_experience", "id_type",
                       "id_number", "trade_credentials", "fee_amount", "fee_paid_at", "payment",
                       "submitted_at", "reviewed_at", "reviewed_by", "review_note",
                       "audit_history")
    actions = ("approve_selected",)

    @admin.display(description="History")
    def audit_history(self, obj):
        rows = [(e.created_at.strftime("%Y-%m-%d %H:%M"), e.action,
                 e.actor.email if e.actor else "system") for e in history(obj)]
        if not rows:
            return "—"
        return format_html("<table>{}</table>", format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td></tr>", rows))

    @admin.action(description="✓ Approve selected (grants Verified Artisan badge)")
    def approve_selected(self, request, queryset):
        n = 0
        for app in queryset.filter(status=ArtisanVerificationStatus.PENDING_REVIEW):
            review_application(app, admin=request.user, approve=True,
                               note="Approved from Django admin", request=request)
            n += 1
        self.message_user(request, f"Approved {n} application(s). Reject with a reason from "
                                   f"/admin/reviews in the web app.", messages.SUCCESS)

    def has_add_permission(self, request):
        return False
