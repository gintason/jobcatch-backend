from django.urls import path

from . import views

urlpatterns = [
    path("referrals/", views.ReferralSummaryView.as_view(), name="referrals"),
    path("referrals/apply/", views.ApplyReferralView.as_view(), name="referrals-apply"),
]
