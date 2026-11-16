"""ASGI entrypoint: HTTP (Django) + WebSocket (Channels) protocols."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django_asgi_app = get_asgi_application()

# Imported after Django is set up (they touch the app registry).
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.messaging.middleware import JWTAuthMiddleware  # noqa: E402
from apps.messaging.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
})
