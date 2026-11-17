"""
Remember which client made the request (web or the mobile app) for the
duration of that request, so checkout can send the gateway the right
return URL. The mobile app sends `X-JobCatch-Client: mobile`.
"""
from contextvars import ContextVar

_client: ContextVar[str] = ContextVar("jobcatch_client", default="web")


def current_client() -> str:
    return _client.get()


class ClientChannelMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        value = (request.headers.get("X-JobCatch-Client") or "").strip().lower()
        token = _client.set("mobile" if value == "mobile" else "web")
        try:
            return self.get_response(request)
        finally:
            _client.reset(token)
