"""Self-service profile management views (Phase 2A)."""
from rest_framework import generics, viewsets
from rest_framework.exceptions import PermissionDenied

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
