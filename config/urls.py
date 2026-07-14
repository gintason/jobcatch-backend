"""Root URL configuration."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
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
# NOTE: django.conf.urls.static.static() is a no-op when DEBUG=False — it returns
# an empty list, silently registering no route at all. So the serve view is wired
# up directly. Needed because uploads live on a Render persistent disk rather
# than object storage; when an S3 bucket IS configured, production.py sets
# SERVE_MEDIA=False and S3 serves the files instead.
if settings.DEBUG or getattr(settings, "SERVE_MEDIA", False):
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
