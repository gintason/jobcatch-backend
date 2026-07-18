"""
CV service API.

  GET  /cv-service/access/       -> is the caller entitled? (+ the price)
  GET  /cv-service/submissions/  -> the seeker's own submissions
  POST /cv-service/submissions/  -> submit a CV to the JobCatch admin (paid only)
  GET  /cv-service/referrals/    -> CVs the admin forwarded to this employer
"""
from rest_framework import generics, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CVReferral, CVServiceAccess, CVSubmission
from .serializers import (
    CVServiceAccessSerializer,
    CVSubmissionSerializer,
    ReferredCVSerializer,
)


def _access_for(user):
    access, _ = CVServiceAccess.objects.get_or_create(user=user)
    return access


class CVServiceAccessView(APIView):
    """Whether the caller has paid for the concierge CV service."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        access = _access_for(request.user)
        return Response(CVServiceAccessSerializer(access).data)


class CVSubmissionViewSet(viewsets.ModelViewSet):
    """A job seeker's CV submissions to the JobCatch admin."""

    serializer_class = CVSubmissionSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return CVSubmission.objects.filter(seeker=self.request.user)

    def create(self, request, *args, **kwargs):
        # Gate on the one-off payment.
        if not _access_for(request.user).is_active:
            return Response(
                {
                    "detail": "This service requires a one-time payment.",
                    "code": "cv_service_unpaid",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(seeker=self.request.user)


class ReferredCVListView(generics.ListAPIView):
    """CVs the JobCatch admin forwarded to the calling employer."""

    serializer_class = ReferredCVSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            CVReferral.objects
            .filter(employer=self.request.user)
            .select_related("submission", "submission__seeker")
        )
