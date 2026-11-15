"""Token construction with JobCatch claims baked into BOTH access and refresh."""
from rest_framework_simplejwt.tokens import RefreshToken

CLAIMS = ("role", "token_version", "is_identity_verified", "is_email_verified")


def _apply_claims(token, user):
    token["role"] = user.role
    token["token_version"] = user.token_version
    token["is_identity_verified"] = user.is_identity_verified
    token["is_email_verified"] = user.is_email_verified
    return token


def tokens_for_user(user):
    """Return (refresh, access) with claims set on each (access does not inherit)."""
    refresh = RefreshToken.for_user(user)
    _apply_claims(refresh, user)
    access = refresh.access_token
    _apply_claims(access, user)
    return refresh, access
