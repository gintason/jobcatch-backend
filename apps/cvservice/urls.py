"""CV service routes, mounted at /api/v1/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("cv-service/submissions", views.CVSubmissionViewSet, basename="cv-submission")

urlpatterns = [
    path("cv-service/access/", views.CVServiceAccessView.as_view(), name="cv-service-access"),
    path("cv-service/referrals/", views.ReferredCVListView.as_view(), name="cv-service-referrals"),
    path("", include(router.urls)),
]
