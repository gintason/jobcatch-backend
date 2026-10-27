"""
Admin review screen for employer KYC.

Reviewers see the full case on one page: entered identity details, automated
CAC/TIN/NIN results, each private document (opened through the
permission-checked download view), and the complete audit history. Decisions
go through apps.kyc.services so the badge, notifications and audit trail stay
consistent whichever screen is used (this one or the in-app /admin/reviews).
"""
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from apps.common.audit import history

from . import services
from .models import CHECK_KEYS, EmployerKYC, KYCDocument, KYCStatus

STATUS_COLOURS = {
    "not_started": "#718096", "in_progress": "#2b6cb0", "pending": "#b7791f",
    "verified": "#2f855a", "rejected": "#9b2c2c",
    "passed": "#2f855a", "failed": "#9b2c2c", "error": "#b7791f", "skipped": "#718096",
}


def _pill(value, label=None):
    return format_html('<b style="color:{}">{}</b>', STATUS_COLOURS.get(value, "#444"),
                       label or value or "—")


class KYCDocumentInline(admin.TabularInline):
    model = KYCDocument
    extra = 0
    fields = ("doc_type", "open_link", "original_name", "status", "review_note", "created_at")
    readonly_fields = ("doc_type", "open_link", "original_name", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def open_link(self, obj):
        url = reverse("kyc-document-download", args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noreferrer">Open &nearr;</a>', url)


@admin.register(EmployerKYC)
class EmployerKYCAdmin(admin.ModelAdmin):
    list_display = ("company", "registered_name", "status_pill", "cac_pill", "tin_pill",
                    "nin_pill", "contact_ok", "submitted_at", "reviewed_by")
    list_filter = ("status", "is_manual_override", "company_type", "submitted_at")
    search_fields = ("registered_name", "cac_number", "tin", "employer__company_name",
                     "employer__user__email", "director_name")
    ordering = ("status", "submitted_at")
    inlines = (KYCDocumentInline,)
    actions = ("approve_selected", "rerun_checks")
    readonly_fields = (
        "employer", "status", "registered_name", "company_type", "cac_number", "tin",
        "director_name", "director_nin", "business_address", "state",
        "contact_email", "contact_phone", "email_verified_at", "phone_verified_at",
        "check_results", "submitted_at", "reviewed_at", "reviewed_by",
        "is_manual_override", "rejection_reason", "audit_history",
    )
    fieldsets = (
        ("Case", {"fields": ("employer", "status", "submitted_at", "reviewed_at",
                             "reviewed_by", "is_manual_override")}),
        ("Corporate identity", {"fields": ("registered_name", "company_type", "cac_number",
                                           "tin", "business_address", "state")}),
        ("Director", {"fields": ("director_name", "director_nin")}),
        ("Contact verification", {"fields": ("contact_email", "email_verified_at",
                                             "contact_phone", "phone_verified_at")}),
        ("Automated checks", {"fields": ("check_results",)}),
        ("Decision", {"fields": ("admin_note", "rejection_reason"),
                      "description": "Approve with the list action. Reject or revoke (which "
                                     "need a reason sent to the employer) from the in-app "
                                     "review dashboard at /admin/reviews."}),
        ("Audit history", {"fields": ("audit_history",)}),
    )

    @admin.display(description="Employer", ordering="employer__company_name")
    def company(self, obj):
        return f"{obj.employer.company_name} ({obj.employer.user.email})"

    @admin.display(description="Status", ordering="status")
    def status_pill(self, obj):
        return _pill(obj.status, obj.get_status_display())

    def _check(self, obj, key):
        return _pill(obj.check_status(key))

    @admin.display(description="CAC")
    def cac_pill(self, obj):
        return self._check(obj, "cac")

    @admin.display(description="TIN")
    def tin_pill(self, obj):
        return self._check(obj, "tin")

    @admin.display(description="NIN")
    def nin_pill(self, obj):
        return self._check(obj, "nin")

    @admin.display(description="Email/phone", boolean=True)
    def contact_ok(self, obj):
        return obj.is_email_verified and obj.is_phone_verified

    @admin.display(description="Results")
    def check_results(self, obj):
        rows = []
        for key in CHECK_KEYS:
            c = (obj.checks or {}).get(key) or {}
            data = ", ".join(f"{k}: {v}" for k, v in (c.get("data") or {}).items() if v)
            rows.append((key.upper(), STATUS_COLOURS.get(c.get("status"), "#444"),
                         c.get("status") or "not run", c.get("message") or "", data,
                         c.get("checked_at") or ""))
        return format_html(
            "<table><tr><th>Check</th><th>Result</th><th>Message</th><th>Record</th>"
            "<th>When</th></tr>{}</table>",
            format_html_join("", '<tr><td>{}</td><td><b style="color:{}">{}</b></td>'
                                 "<td>{}</td><td>{}</td><td>{}</td></tr>", rows),
        )

    @admin.display(description="History")
    def audit_history(self, obj):
        rows = [(e.created_at.strftime("%Y-%m-%d %H:%M"), e.action,
                 e.actor.email if e.actor else "system",
                 ", ".join(f"{k}={v}" for k, v in (e.metadata or {}).items()))
                for e in history(obj)]
        if not rows:
            return "—"
        return format_html("<table>{}</table>", format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>", rows))

    @admin.action(description="✓ Approve selected (grants Verified Employer badge)")
    def approve_selected(self, request, queryset):
        n = 0
        for kyc in queryset.exclude(status=KYCStatus.VERIFIED):
            services.approve(kyc, actor=request.user, note="Approved from Django admin",
                             request=request, override=True)
            n += 1
        self.message_user(request, f"Approved {n} case(s).", messages.SUCCESS)

    @admin.action(description="↻ Re-run automated CAC/TIN/NIN checks")
    def rerun_checks(self, request, queryset):
        for kyc in queryset:
            services.run_automated_checks(kyc, actor=request.user, request=request)
        self.message_user(request, f"Re-ran checks on {queryset.count()} case(s).")

    def has_add_permission(self, request):
        return False
