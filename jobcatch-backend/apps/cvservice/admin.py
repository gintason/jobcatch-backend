"""
JobCatch admin screens for the concierge CV service.

The forwarding workflow lives here: open a CV submission, add one or more
"referrals" (employer + optional note), save. The employer then sees the CV in
their dashboard under "Referred CVs".
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import CVReferral, CVServiceAccess, CVSubmission, SubmissionStatus


@admin.register(CVServiceAccess)
class CVServiceAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "is_active", "paid_at", "payment_reference")
    list_filter = ("is_active",)
    search_fields = ("user__email", "user__full_name", "payment_reference")
    autocomplete_fields = ("user",)


class CVReferralInline(admin.TabularInline):
    """Forward this CV to employers, right from the submission page."""

    model = CVReferral
    extra = 1
    autocomplete_fields = ("employer",)
    fields = ("employer", "admin_note")


@admin.register(CVSubmission)
class CVSubmissionAdmin(admin.ModelAdmin):
    list_display = ("seeker", "headline", "status", "referral_count", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("seeker__email", "seeker__full_name", "headline")
    autocomplete_fields = ("seeker",)
    readonly_fields = ("created_at", "updated_at", "cv_link")
    inlines = (CVReferralInline,)
    actions = ("mark_reviewed", "mark_forwarded")

    @admin.display(description="CV file")
    def cv_link(self, obj):
        if not obj.cv_file:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener">Open CV</a>', obj.cv_file.url)

    @admin.display(description="Referrals")
    def referral_count(self, obj):
        return obj.referrals.count()

    @admin.action(description="Mark selected as reviewed")
    def mark_reviewed(self, request, queryset):
        updated = queryset.update(status=SubmissionStatus.REVIEWED)
        self.message_user(request, f"{updated} submission(s) marked reviewed.")

    @admin.action(description="Mark selected as forwarded")
    def mark_forwarded(self, request, queryset):
        updated = queryset.update(status=SubmissionStatus.FORWARDED)
        self.message_user(request, f"{updated} submission(s) marked forwarded.")


@admin.register(CVReferral)
class CVReferralAdmin(admin.ModelAdmin):
    list_display = ("submission", "employer", "created_at")
    search_fields = ("employer__email", "submission__seeker__email")
    autocomplete_fields = ("submission", "employer")
