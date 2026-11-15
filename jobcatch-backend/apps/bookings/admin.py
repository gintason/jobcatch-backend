from django.contrib import admin
from .models import Booking, BookingStatusHistory


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "customer", "artisan", "scheduled_for", "agreed_price")
    list_filter = ("status",)


admin.site.register(BookingStatusHistory)
