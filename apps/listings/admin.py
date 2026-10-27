from django.contrib import admin

from .models import JobPackage, PackagePurchase


@admin.register(JobPackage)
class JobPackageAdmin(admin.ModelAdmin):
    """Package tiers are managed here — price/credit changes need no deploy."""

    list_display = ("name", "price", "job_credits", "listing_days", "validity_days",
                    "is_featured", "limit_per_employer", "is_active", "sort_order")
    list_editable = ("price", "is_active", "sort_order")
    list_filter = ("is_active", "is_featured")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(PackagePurchase)
class PackagePurchaseAdmin(admin.ModelAdmin):
    list_display = ("employer", "package", "status", "credits_used", "credits_total",
                    "price", "activated_at", "expires_at")
    list_filter = ("status", "package", "is_featured")
    search_fields = ("employer__company_name", "employer__user__email", "payment__reference")
    readonly_fields = ("employer", "package", "payment", "price", "credits_total",
                       "listing_days", "is_featured", "validity_days", "activated_at",
                       "created_at")
