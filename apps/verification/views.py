"""
Verification workflow.

  POST /verifications/                 -> user submits a document (pending)
  GET  /verifications/                 -> own submissions (admin: all, ?status= filter)
  POST /verifications/{id}/approve/    -> admin approves -> grants the badge
  POST /verifications/{id}/reject/     -> admin rejects (with optional note)

  GET  /admin/job-videos/              -> admin lists artisan job videos (?status=)
  POST /admin/job-videos/{id}/approve/ -> admin approves -> is_work_verified badge
  POST /admin/job-videos/{id}/reject/
"""
from rest_framework import status as http, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.api_serializers import ArtisanJobVideoSerializer
from apps.accounts.models import ArtisanJobVideo, UserRole
from apps.common.permissions import IsAdmin

from .models import Verification, VerificationStatus
from .serializers import VerificationCreateSerializer, VerificationSerializer
from .services import apply_verification_badge, approve_job_video, reject_job_video


class VerificationViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        return VerificationCreateSerializer if self.action == "create" else VerificationSerializer

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Verification.objects.select_related("user", "reviewed_by")
        if self.request.user.role == UserRole.ADMIN:
            status_param = self.request.query_params.get("status")
            return qs.filter(status=status_param) if status_param else qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, status=VerificationStatus.PENDING)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        v = self.get_object()
        v.status = VerificationStatus.APPROVED
        v.reviewed_by = request.user
        v.review_note = request.data.get("note", "")
        v.save(update_fields=["status", "reviewed_by", "review_note", "updated_at"])
        apply_verification_badge(v)
        return Response(VerificationSerializer(v).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        v = self.get_object()
        v.status = VerificationStatus.REJECTED
        v.reviewed_by = request.user
        v.review_note = request.data.get("note", "")
        v.save(update_fields=["status", "reviewed_by", "review_note", "updated_at"])
        return Response(VerificationSerializer(v).data)


class AdminJobVideoViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin review queue for artisan job-sample videos."""

    permission_classes = [IsAdmin]
    serializer_class = ArtisanJobVideoSerializer

    def get_queryset(self):
        qs = ArtisanJobVideo.objects.select_related("artisan__user")
        status_param = self.request.query_params.get("status")
        return qs.filter(status=status_param) if status_param else qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        video = self.get_object()
        approve_job_video(video, request.user, request.data.get("note", ""))
        return Response(ArtisanJobVideoSerializer(video).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        video = self.get_object()
        reject_job_video(video, request.user, request.data.get("note", ""))
        return Response(ArtisanJobVideoSerializer(video).data)
