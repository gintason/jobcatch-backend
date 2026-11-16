from django.contrib import admin

from .models import Referral, ReferralCode, ReferralStatus


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referee", "code", "status", "reward_amount", "referee_bonus", "created_at", "rewarded_at")
    list_filter = ("status",)
    search_fields = ("referrer__email", "referee__email", "code")
    readonly_fields = ("referrer", "referee", "code", "reward_amount", "referee_bonus", "qualifying_reference", "rewarded_at")
    actions = ["reject"]

    @admin.action(description="Mark as not eligible (only pending referrals)")
    def reject(self, request, queryset):
        n = queryset.filter(status=ReferralStatus.PENDING).update(status=ReferralStatus.REJECTED,
                                                                  note="Rejected by admin.")
        self.message_user(request, f"{n} referral(s) marked not eligible.")


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "created_at")
    search_fields = ("code", "user__email")
