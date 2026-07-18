"""Self-service profile management views (Phase 2A)."""
from rest_framework import generics, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import UserRole

from apps.common.permissions import IsArtisan

from .api_serializers import (
    PROFILE_MAP,
    ArtisanJobVideoSerializer,
    ArtisanPortfolioItemSerializer,
)
from .models import ArtisanJobVideo, ArtisanPortfolioItem


class MyProfileView(generics.RetrieveUpdateAPIView):
    """
    GET/PUT/PATCH the CALLER's own role profile. Role-aware: it picks the right
    serializer and object based on request.user.role. Admins have no profile.
    """

    def get_serializer_class(self):
        mapping = PROFILE_MAP.get(self.request.user.role)
        if not mapping:
            raise PermissionDenied("This account type has no editable profile.")
        return mapping[1]

    def get_object(self):
        attr, _ = PROFILE_MAP[self.request.user.role]
        return getattr(self.request.user, attr)


class _ArtisanOwnedViewSet(viewsets.ModelViewSet):
    """Base for artisan sub-resources: scoped to the caller's own artisan profile."""

    permission_classes = [IsArtisan]

    def get_queryset(self):
        return self.queryset.filter(artisan=self.request.user.artisan_profile)

    def perform_create(self, serializer):
        serializer.save(artisan=self.request.user.artisan_profile)


class ArtisanPortfolioViewSet(_ArtisanOwnedViewSet):
    queryset = ArtisanPortfolioItem.objects.all()
    serializer_class = ArtisanPortfolioItemSerializer


class ArtisanJobVideoViewSet(_ArtisanOwnedViewSet):
    """
    Artisans upload/list/delete their own job-sample videos. Approval (status)
    is handled by admins in the verification workflow, not here.
    """

    queryset = ArtisanJobVideo.objects.all()
    serializer_class = ArtisanJobVideoSerializer

class SwitchRoleView(APIView):
    """
    Switch the caller's ACTIVE role (single-account, multi-mode platform).

    Role is a mode, not an identity. Every user owns all four profiles, so this
    only flips which mode permissions resolve against — no data is created or
    destroyed. Permissions read `role` from the DB, not the JWT claim, so the
    switch takes effect on the very next request with no token refresh.
    """

    permission_classes = [IsAuthenticated]

    SWITCHABLE = {
        UserRole.CUSTOMER,
        UserRole.ARTISAN,
        UserRole.EMPLOYER,
        UserRole.JOB_SEEKER,
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