"""
JWT authentication that additionally enforces `token_version`.

DEFAULT_AUTHENTICATION_CLASSES points here. On every request we compare the
token's token_version claim to the user's current value; a mismatch means the
token was revoked (logout-all / password reset) and is rejected even if it is
otherwise unexpired and correctly signed.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        claimed = validated_token.get("token_version")
        if claimed is None or int(claimed) != int(user.token_version):
            raise AuthenticationFailed("Token has been revoked.", code="token_revoked")
        return user
