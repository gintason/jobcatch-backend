from django.contrib import admin
from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("target", "author", "rating", "is_visible", "created_at")
    list_filter = ("rating", "is_visible")
