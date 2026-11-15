from django.contrib import admin
from .models import Application, CV, Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "employer", "is_open", "created_at")
    list_filter = ("is_open",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("seeker", "job", "status", "created_at")
    list_filter = ("status",)


admin.site.register(CV)
