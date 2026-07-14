"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
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
    path("api/v1/", include("apps.payments.urls")),        # payments + webhook
    path("api/v1/", include("apps.subscriptions.urls")),   # subscription history
    path("api/v1/", include("apps.verification.urls")),    # verification workflow
    path("api/v1/", include("apps.notifications.urls")),   # notification inbox
    path("api/v1/", include("apps.matching.urls")),        # geo-matching engine
    path("api/v1/", include("apps.messaging.urls")),       # conversations + message history
    path("api/v1/", include("apps.ai.urls")),              # AI chatbot (RAG + tools)
    path("api/v1/", include("apps.public.urls")),          # public home-page data (no auth)

    # --- OpenAPI schema + docs ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

# Serve user uploads (photos, CVs, videos) from the app itself.
#
# Django refuses to serve MEDIA_URL when DEBUG=False — normally that's the job of
# nginx or object storage. With uploads on a Render persistent disk and no S3
# bucket configured, Django has to do it, so SERVE_MEDIA opts back in. It's set
# automatically in production.py only when no S3 bucket is present.
if settings.DEBUG or getattr(settings, "SERVE_MEDIA", False):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
