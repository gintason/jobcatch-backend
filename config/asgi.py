"""ASGI entrypoint. HTTP now; WebSocket routing added in Phase 3 (messaging)."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # "websocket": ...  # wired in Phase 3
})
