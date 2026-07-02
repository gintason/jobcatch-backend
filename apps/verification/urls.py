from rest_framework.routers import DefaultRouter

from .views import AdminJobVideoViewSet, VerificationViewSet

router = DefaultRouter()
router.register("verifications", VerificationViewSet, basename="verification")
# Separate prefix (not nested under verifications) to avoid detail-route collision.
router.register("admin/job-videos", AdminJobVideoViewSet, basename="admin-job-video")

urlpatterns = router.urls
