from django.contrib import admin

from .models import Delivery, DeliveryRate, RiderProfile


@admin.register(DeliveryRate)
class DeliveryRateAdmin(admin.ModelAdmin):
    list_display = ("vehicle_type", "base_fare", "per_km", "minimum_fare", "errand_fee", "is_active")
    list_editable = ("base_fare", "per_km", "minimum_fare", "errand_fee", "is_active")


@admin.register(RiderProfile)
class RiderProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "vehicle_type", "plate_number", "status", "is_online", "completed_count",
                    "city")
    list_filter = ("status", "vehicle_type", "is_online")
    search_fields = ("user__email", "user__full_name", "plate_number", "phone", "id_number")
    readonly_fields = ("subaccount_code", "account_name", "account_number", "bank_code",
                       "reviewed_at", "reviewed_by", "completed_count", "rating_sum", "rating_count")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "customer", "rider", "kind", "vehicle_type", "fare", "status")
    list_filter = ("status", "kind", "vehicle_type", "created_at")
    search_fields = ("customer__email", "rider__user__email", "pickup_address", "dropoff_address",
                     "payment__reference")
    readonly_fields = [f.name for f in Delivery._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
