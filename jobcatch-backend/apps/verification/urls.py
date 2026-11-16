from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("verifications", views.VerificationViewSet, basename="verification")
# Separate prefix (not nested under verifications) to avoid detail-route collision.
router.register("admin/job-videos", views.AdminJobVideoViewSet, basename="admin-job-video")
router.register("artisan-verification/documents", views.MyArtisanDocumentViewSet,
                basename="artisan-verification-document")
router.register("admin/artisan-verifications", views.AdminArtisanVerificationViewSet,
                basename="admin-artisan-verification")

urlpatterns = [
    path("artisan-verification/", views.MyArtisanVerificationView.as_view(),
         name="artisan-verification"),
    path("artisan-verification/submit/", views.SubmitArtisanVerificationView.as_view(),
         name="artisan-verification-submit"),
    path("artisan-verification/documents/<uuid:pk>/download/",
         views.ArtisanDocumentDownloadView.as_view(), name="artisan-document-download"),
    *router.urls,
]
