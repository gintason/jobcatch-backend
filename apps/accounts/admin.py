"""Admin for users, profiles, and artisan job-sample videos."""
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html

from .models import (
    ArtisanJobVideo, ArtisanProfile, CustomerProfile, EmployerProfile,
    JobSeekerProfile, JobVideoStatus, User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "role", "is_email_verified",
                    "is_identity_verified", "is_active", "created_at")
    list_filter = ("role", "is_email_verified", "is_identity_verified", "is_active", "is_staff")
    search_fields = ("email", "full_name", "phone")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("full_name", "phone", "role")}),
        ("Verification", {"fields": ("is_email_verified", "is_phone_verified",
                                     "is_identity_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                    "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "role", "password1", "password2"),
        }),
    )


@admin.register(ArtisanProfile)
class ArtisanProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "avg_rating", "rating_count", "is_available",
                    "is_work_verified", "is_featured", "service_radius_km")
    list_filter = ("is_available", "is_work_verified", "is_featured")
    search_fields = ("user__email", "user__full_name")
    readonly_fields = ("avg_rating", "rating_count", "reputation_score")


@admin.register(ArtisanJobVideo)
class ArtisanJobVideoAdmin(admin.ModelAdmin):
    """Review queue for artisans' work-sample videos (grants the work badge)."""

    list_display = ("artisan", "title", "status_badge", "video_link",
                    "reviewed_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("artisan__user__email", "artisan__user__full_name", "title")
    readonly_fields = ("artisan", "video", "video_link", "created_at")
    actions = ("approve_videos", "reject_videos")
    ordering = ("status", "-created_at")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colours = {"pending": "#b7791f", "approved": "#2f855a", "rejected": "#9b2c2c"}
        return format_html('<b style="color:{}">{}</b>',
                           colours.get(obj.status, "#444"), obj.get_status_display())

    @admin.display(description="Video")
    def video_link(self, obj):
        if not obj.video:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noreferrer">Watch &nearr;</a>',
                           obj.video.url)

    @admin.action(description="✓ Approve selected (grants the work-verified badge)")
    def approve_videos(self, request, queryset):
        from apps.verification.services import approve_job_video

        n = 0
        for video in queryset.exclude(status=JobVideoStatus.APPROVED):
            approve_job_video(video, request.user)
            n += 1
        self.message_user(request, f"Approved {n} video(s).", messages.SUCCESS)

    @admin.action(description="✗ Reject selected")
    def reject_videos(self, request, queryset):
        from apps.verification.services import reject_job_video

        n = 0
        for video in queryset.exclude(status=JobVideoStatus.REJECTED):
            reject_job_video(video, request.user)
            n += 1
        self.message_user(request, f"Rejected {n} video(s).", messages.WARNING)


admin.site.register(CustomerProfile)
admin.site.register(EmployerProfile)
admin.site.register(JobSeekerProfile)

admin.site.site_header = "JobCatch administration"
admin.site.site_title = "JobCatch admin"
admin.site.index_title = "Platform management"
