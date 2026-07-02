"""Root URL configuration."""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),

    # --- API v1 ---
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.accounts.api_urls")),   # profiles, portfolio, job-videos
    path("api/v1/", include("apps.catalog.urls")),         # categories, services
    path("api/v1/", include("apps.jobs.urls")),            # cvs (jobs/applications in 2C)
    path("api/v1/", include("apps.bookings.urls")),        # bookings + lifecycle actions
    path("api/v1/", include("apps.reviews.urls")),         # reviews

    # --- OpenAPI schema + docs ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
