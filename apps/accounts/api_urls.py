"""Profile-management routes, mounted at /api/v1/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api_views

router = DefaultRouter()
router.register("artisan/portfolio", api_views.ArtisanPortfolioViewSet, basename="portfolio")
router.register("artisan/job-videos", api_views.ArtisanJobVideoViewSet, basename="job-video")

urlpatterns = [
    path("me/profile/", api_views.MyProfileView.as_view(), name="my-profile"),
    path("", include(router.urls)),
]
