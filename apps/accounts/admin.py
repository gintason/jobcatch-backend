from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    ArtisanJobVideo,
    ArtisanPortfolioItem,
    ArtisanProfile,
    CustomerProfile,
    EmployerProfile,
    JobSeekerProfile,
    OTP,
    SessionRecord,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-created_at",)
    list_display = ("email", "full_name", "role", "is_email_verified",
                    "is_identity_verified", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_email_verified", "is_identity_verified")
    search_fields = ("email", "full_name", "phone")
    readonly_fields = ("id", "last_login", "created_at", "updated_at", "token_version")
    fieldsets = (
        (None, {"fields": ("id", "email", "phone", "password")}),
        ("Identity", {"fields": ("full_name", "role")}),
        ("Verification", {"fields": ("is_email_verified", "is_phone_verified", "is_identity_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Security", {"fields": ("token_version", "last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "full_name", "role", "password1", "password2")}),
    )


@admin.register(ArtisanJobVideo)
class ArtisanJobVideoAdmin(admin.ModelAdmin):
    list_display = ("title", "artisan", "status", "reviewed_by", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "artisan__user__email")


admin.site.register(CustomerProfile)
admin.site.register(ArtisanProfile)
admin.site.register(ArtisanPortfolioItem)
admin.site.register(EmployerProfile)
admin.site.register(JobSeekerProfile)
admin.site.register(SessionRecord)
admin.site.register(OTP)
