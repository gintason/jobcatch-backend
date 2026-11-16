"""
Authentication endpoints.

Token placement (architecture §3.2):
  - Refresh token is ALWAYS set as an HttpOnly, Secure, SameSite cookie
    (web clients rely on this and never touch it from JS).
  - Refresh token is ALSO returned in the JSON body so React Native can store
    it in expo-secure-store. Web clients simply ignore the body copy.
"""
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import generics, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import UserRole

from .models import OTP, OTPPurpose, SessionRecord, User
from .serializers import (
    ChangePasswordSerializer,
    ProfileUpdateSerializer,
    LoginSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    ResendOTPSerializer,
    VerifyOTPSerializer,
)
from .tasks import deliver_otp
from .throttles import LoginThrottle, OTPThrottle
from .tokens import tokens_for_user

logger = logging.getLogger(__name__)

REFRESH_COOKIE = "jc_refresh"


# ------------------------------------------------------------------ helpers
def _issue_otp(user, purpose):
    otp, code = OTP.issue(user, purpose, settings.OTP_LENGTH, settings.OTP_TTL_MINUTES)
    deliver_otp(user.email, code, purpose)
    return otp


def _set_refresh_cookie(response, refresh_token):
    response.set_cookie(
        REFRESH_COOKIE,
        str(refresh_token),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        path="/api/v1/auth/",
    )
    return response


def _auth_response(user, request, created=False):
    refresh, access = tokens_for_user(user)
    SessionRecord.objects.create(
        user=user,
        refresh_jti=refresh["jti"],
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:400],
    )
    body = {
        "access": str(access),
        "refresh": str(refresh),  # for mobile secure-store; web ignores this
        "user": MeSerializer(user).data,
    }
    resp = Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
    return _set_refresh_cookie(resp, refresh)


def _client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


# ------------------------------------------------------------------ views
class RegisterView(generics.CreateAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        _issue_otp(user, OTPPurpose.EMAIL_VERIFY)
        return Response(
            {"detail": "Registered. Check your email for a verification code.",
             "user_id": str(user.id)},
            status=status.HTTP_201_CREATED,
        )


class VerifyOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        s = VerifyOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        if not user:
            return Response({"detail": "Invalid code."}, status=status.HTTP_400_BAD_REQUEST)
        purpose = s.validated_data["purpose"]
        otp = (
            OTP.objects.filter(user=user, purpose=purpose, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if not otp or not otp.verify(s.validated_data["code"]):
            return Response({"detail": "Invalid or expired code."},
                            status=status.HTTP_400_BAD_REQUEST)
        if purpose == OTPPurpose.EMAIL_VERIFY:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified", "updated_at"])
            return _auth_response(user, request)  # auto-login on successful verify
        return Response({"detail": "Verified."}, status=status.HTTP_200_OK)


class ResendOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        s = ResendOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        # Do not reveal whether the account exists.
        if user:
            _issue_otp(user, s.validated_data["purpose"])
        return Response({"detail": "If the account exists, a code has been sent."})


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        if not user or not user.check_password(s.validated_data["password"]):
            return Response({"detail": "Invalid credentials."},
                            status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            return Response({"detail": "Account is disabled."},
                            status=status.HTTP_403_FORBIDDEN)
        if not user.is_email_verified:
            # Send a fresh code as we bounce them to the verify screen — otherwise
            # they land there waiting for an email that was never sent.
            _issue_otp(user, OTPPurpose.EMAIL_VERIFY)
            return Response({"detail": "Email not verified. We've sent you a new code.",
                             "code": "email_unverified"},
                            status=status.HTTP_403_FORBIDDEN)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return _auth_response(user, request)


class GoogleSignInView(APIView):
    """
    POST {credential: <Google ID token>, role?} -> same response as login.
    Signs in the account with that (Google-verified) email, or creates one.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        from .google import GoogleTokenError, verify_id_token

        try:
            info = verify_id_token(str(request.data.get("credential") or ""))
        except GoogleTokenError as exc:
            return Response({"detail": str(exc), "code": "google_failed"},
                            status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(email__iexact=info["email"]).first()
        created = False
        if user is None:
            role = request.data.get("role")
            user = User(email=info["email"], full_name=(info["name"] or info["email"].split("@")[0])[:150],
                        role=role if role in SwitchRoleView.SWITCHABLE else UserRole.CUSTOMER,
                        is_email_verified=True)
            user.set_unusable_password()       # they can add one later via "forgot password"
            user.save()                         # post_save creates the role profiles
            created = True
            code = str(request.data.get("referral_code") or "").strip()
            if code:
                from apps.referrals.services import attach
                try:
                    attach(user, code)
                except Exception:  # noqa: BLE001 — a bad code must not block sign-in
                    logger.info("Ignored referral code on Google sign-up")
        elif not user.is_active:
            return Response({"detail": "Account is disabled."}, status=status.HTTP_403_FORBIDDEN)
        # Google has proved they own this email address.
        user.is_email_verified = True
        user.last_login = timezone.now()
        user.save(update_fields=["is_email_verified", "last_login"])
        return _auth_response(user, request, created=created)


class CookieTokenRefreshView(APIView):
    """Reads refresh from the cookie (web) or the request body (mobile)."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE) or request.data.get("refresh")
        if not raw:
            return Response({"detail": "No refresh token."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            old = RefreshToken(raw)
            old.verify()
        except TokenError:
            return Response({"detail": "Invalid or expired refresh token."},
                            status=status.HTTP_401_UNAUTHORIZED)
        # Rotation + blacklist are handled by SimpleJWT settings on blacklist();
        # here we mint a fresh pair carrying current claims.
        user = User.objects.filter(id=old["user_id"]).first()
        claimed = old.get("token_version")
        # A deactivated/deleted account, or a token issued before "log out of
        # all devices" (token_version bumped), must not mint new tokens.
        if (user is None or not user.is_active
                or claimed is None or int(claimed) != int(user.token_version)):
            return Response({"detail": "Session has ended. Please sign in again."},
                            status=status.HTTP_401_UNAUTHORIZED)
        try:
            old.blacklist()
        except Exception:  # noqa: BLE001 (blacklist app may be catching reuse)
            pass
        refresh, access = tokens_for_user(user)
        SessionRecord.objects.filter(refresh_jti=old["jti"]).update(refresh_jti=refresh["jti"])
        resp = Response({"access": str(access), "refresh": str(refresh)})
        return _set_refresh_cookie(resp, refresh)


class LogoutView(APIView):
    """Revoke the current device: blacklist its refresh + drop the session row."""

    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE) or request.data.get("refresh")
        if raw:
            try:
                token = RefreshToken(raw)
                SessionRecord.objects.filter(user=request.user, refresh_jti=token["jti"]).delete()
                token.blacklist()
            except TokenError:
                pass
        resp = Response({"detail": "Logged out."})
        resp.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/")
        return resp


class LogoutAllView(APIView):
    """Global logout: bump token_version so every outstanding JWT is invalid."""

    def post(self, request):
        request.user.bump_token_version()
        SessionRecord.objects.filter(user=request.user).delete()
        resp = Response({"detail": "Logged out of all devices."})
        resp.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/")
        return resp


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        s = PasswordResetRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        if user:
            _issue_otp(user, OTPPurpose.PASSWORD_RESET)
        return Response({"detail": "If the account exists, a reset code has been sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        s = PasswordResetConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        otp = None
        if user:
            otp = (
                OTP.objects.filter(user=user, purpose=OTPPurpose.PASSWORD_RESET, is_used=False)
                .order_by("-created_at")
                .first()
            )
        if not user or not otp or not otp.verify(s.validated_data["code"]):
            return Response({"detail": "Invalid or expired code."},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(s.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        user.bump_token_version()  # force re-login everywhere after reset
        SessionRecord.objects.filter(user=user).delete()
        return Response({"detail": "Password reset. Please log in again."})


class MeView(generics.RetrieveAPIView):
    """GET /auth/me/ — the signed-in account. PATCH — edit name, phone and photo."""
    serializer_class = MeSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        return self.request.user

    def patch(self, request):
        s = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response(MeSerializer(user, context={"request": request}).data)


class ChangePasswordView(APIView):
    """POST /auth/change-password/ {current_password, new_password}.

    Signs out every other device; this device gets fresh tokens so it stays signed in.
    """

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        user = request.user
        user.set_password(s.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        user.bump_token_version()
        SessionRecord.objects.filter(user=user).delete()
        return _auth_response(user, request)


class SessionListView(APIView):
    """List / revoke this user's active sessions (device management)."""

    def get(self, request):
        rows = SessionRecord.objects.filter(user=request.user).order_by("-last_seen")
        data = [
            {"id": str(r.id), "device": r.device, "ip_address": r.ip_address,
             "user_agent": r.user_agent, "last_seen": r.last_seen}
            for r in rows
        ]
        return Response(data)

    def delete(self, request, session_id):
        deleted, _ = SessionRecord.objects.filter(user=request.user, id=session_id).delete()
        if not deleted:
            return Response({"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

class SwitchRoleView(APIView):
    """
    Switch the caller's ACTIVE role (single-account, multi-mode platform).

    Role is a mode, not an identity. Every user owns all four profiles, so this
    only flips which mode permissions resolve against — no data is created or
    destroyed. Permissions read `role` from the DB rather than the JWT claim,
    so the switch applies on the very next request with no token refresh.
    """

    permission_classes = [IsAuthenticated]

    SWITCHABLE = {
        UserRole.CUSTOMER,
        UserRole.ARTISAN,
        UserRole.EMPLOYER,
        UserRole.JOB_SEEKER,
        UserRole.RIDER,
    }

    def post(self, request):
        role = request.data.get("role")
        if role not in self.SWITCHABLE:
            return Response(
                {"detail": "Invalid role.", "code": "invalid_role"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        if user.role != role:
            user.role = role
            user.save(update_fields=["role"])
        return Response({"id": str(user.id), "role": user.role})

class DeleteAccountView(APIView):
    """
    POST /auth/delete-account/ — the user closes their own account (required by
    the App Store and Google Play). Password accounts confirm with their
    password; Google-only accounts type "DELETE".

    Financial records (payments, wallet ledger, bill orders) must be kept for
    accounting and dispute purposes, so the account is anonymised and
    deactivated rather than hard-deleted: personal details are wiped, every
    session is ended, and public listings (services, jobs, rider status) are
    taken down.
    """

    def post(self, request):
        user = request.user
        if user.has_usable_password():
            if not user.check_password(str(request.data.get("password") or "")):
                return Response({"detail": "Incorrect password."}, status=status.HTTP_400_BAD_REQUEST)
        elif str(request.data.get("confirm") or "").strip().upper() != "DELETE":
            return Response({"detail": 'Type DELETE to confirm.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.catalog.models import Service as ArtisanService
        from apps.dispatch.models import RiderProfile
        from apps.jobs.models import Job

        ArtisanService.objects.filter(artisan__user=user).update(is_active=False)
        Job.objects.filter(employer__user=user).update(is_open=False)
        RiderProfile.objects.filter(user=user).update(is_online=False)

        if user.photo:
            user.photo.delete(save=False)
        user.email = f"deleted-{user.id}@deleted.jobcatchonline.com"
        user.full_name = "Deleted user"
        user.phone = None
        user.is_active = False
        user.set_unusable_password()
        user.save()
        user.bump_token_version()
        SessionRecord.objects.filter(user=user).delete()
        resp = Response({"detail": "Your account has been deleted."})
        resp.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/")
        return resp
