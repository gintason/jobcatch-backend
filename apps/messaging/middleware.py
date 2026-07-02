"""JWT authentication for the WebSocket handshake (token passed as ?token=)."""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _get_user(token):
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken

    from apps.accounts.models import User

    if not token:
        return AnonymousUser()
    try:
        access = AccessToken(token)
    except TokenError:
        return AnonymousUser()
    user = User.objects.filter(id=access.get("user_id")).first()
    if not user:
        return AnonymousUser()
    # Enforce token_version, same as the REST auth class.
    tv = access.get("token_version")
    if tv is None or int(tv) != int(user.token_version):
        return AnonymousUser()
    return user


class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        token = query.get("token", [None])[0]
        scope["user"] = await _get_user(token)
        return await self.app(scope, receive, send)
