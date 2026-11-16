from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("kyc/employer/documents", views.MyKYCDocumentViewSet, basename="kyc-document")
router.register("admin/kyc", views.AdminKYCViewSet, basename="admin-kyc")

urlpatterns = [
    path("kyc/employer/", views.MyKYCView.as_view(), name="kyc-employer"),
    path("kyc/employer/otp/send/", views.SendOTPView.as_view(), name="kyc-otp-send"),
    path("kyc/employer/otp/verify/", views.VerifyOTPView.as_view(), name="kyc-otp-verify"),
    path("kyc/employer/submit/", views.SubmitKYCView.as_view(), name="kyc-submit"),
    path("kyc/documents/<uuid:pk>/download/", views.DocumentDownloadView.as_view(),
         name="kyc-document-download"),
    # Before the router so "documents" isn't read as an admin/kyc/<pk>.
    path("admin/kyc/documents/<uuid:pk>/review/", views.AdminDocumentReviewView.as_view(),
         name="admin-kyc-document-review"),
    *router.urls,
]
