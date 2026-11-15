"""
Verification workflow.

  POST /verifications/                 -> user submits a document (pending)
  GET  /verifications/                 -> own submissions (admin: all, ?status= filter)
  POST /verifications/{id}/approve/    -> admin approves -> grants the badge
  POST /verifications/{id}/reject/     -> admin rejects (with optional note)

  GET  /admin/job-videos/              -> admin lists artisan job videos (?status=)
  POST /admin/job-videos/{id}/approve/ -> admin approves -> is_work_verified badge
  POST /admin/job-videos/{id}/reject/

Paid artisan verification (Verified Artisan badge) — see the section at the
bottom of this module.
"""
import mimetypes

from django.http import FileResponse, Http404
from rest_framework import mixins, status as http, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api_serializers import ArtisanJobVideoSerializer
from apps.accounts.authentication import VersionedJWTAuthentication
from apps.accounts.models import ArtisanJobVideo, UserRole
from apps.common import audit
from apps.common.permissions import IsAdmin, IsArtisan
from apps.payments.services import CheckoutError

from .models import (
    ArtisanVerificationApplication,
    ArtisanVerificationDocument,
    ArtisanVerificationStatus,
    Verification,
    VerificationStatus,
)
from .serializers import (
    AdminArtisanVerificationSerializer,
    ArtisanVerificationDocumentSerializer,
    ArtisanVerificationSerializer,
    SubmitArtisanVerificationSerializer,
    VerificationCreateSerializer,
    VerificationSerializer,
)
from .services import (
    application_for,
    apply_verification_badge,
    approve_job_video,
    reject_job_video,
    review_application,
    revoke_artisan_verification,
    submit_application,
)


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


# ---------------------------------------------------------------------------
# Paid artisan verification
#
#   GET/PATCH /artisan-verification/                 -> own application
#   POST/DELETE /artisan-verification/documents/[id]/
#   POST /artisan-verification/submit/               -> {gateway} -> checkout URL or queued
#   GET  /artisan-verification/documents/<id>/download/  (owner or admin)
#
#   GET  /admin/artisan-verifications/?status=pending_review
#   POST /admin/artisan-verifications/<id>/approve/  {note}
#   POST /admin/artisan-verifications/<id>/reject/   {note}
#   POST /admin/artisan-verifications/<id>/revoke/   {note}
# ---------------------------------------------------------------------------
def _locked():
    return Response({"detail": "Your verification is under review or complete."},
                    status=http.HTTP_400_BAD_REQUEST)


class MyArtisanVerificationView(APIView):
    permission_classes = [IsArtisan]

    def get(self, request):
        return Response(ArtisanVerificationSerializer(
            application_for(request.user.artisan_profile)).data)

    def patch(self, request):
        app = application_for(request.user.artisan_profile)
        if not app.is_editable:
            return _locked()
        ser = ArtisanVerificationSerializer(app, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit.record(app, "artisan_verification.updated", request=request)
        return Response(ArtisanVerificationSerializer(app).data)


class SubmitArtisanVerificationView(APIView):
    permission_classes = [IsArtisan]

    def post(self, request):
        ser = SubmitArtisanVerificationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        app = application_for(request.user.artisan_profile)
        try:
            result = submit_application(app, user=request.user,
                                        gateway=ser.validated_data.get("gateway"), request=request)
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=http.HTTP_502_BAD_GATEWAY)
        data = ArtisanVerificationSerializer(result["application"]).data
        data["authorization_url"] = result["authorization_url"]
        return Response(data)


class MyArtisanDocumentViewSet(mixins.CreateModelMixin, mixins.DestroyModelMixin,
                               mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsArtisan]
    serializer_class = ArtisanVerificationDocumentSerializer

    def get_queryset(self):
        return ArtisanVerificationDocument.objects.filter(
            application__artisan__user=self.request.user)

    def perform_create(self, serializer):
        app = application_for(self.request.user.artisan_profile)
        if not app.is_editable:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"detail": "Your verification is under review or complete."})
        doc = serializer.save(application=app)
        audit.record(app, "artisan_verification.document_uploaded", request=self.request,
                     doc_type=doc.doc_type)

    def perform_destroy(self, instance):
        if not instance.application.is_editable:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"detail": "Documents are locked while under review."})
        audit.record(instance.application, "artisan_verification.document_deleted",
                     request=self.request, doc_type=instance.doc_type)
        instance.file.delete(save=False)
        instance.delete()


class ArtisanDocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [VersionedJWTAuthentication, SessionAuthentication]

    def get(self, request, pk):
        doc = (ArtisanVerificationDocument.objects
               .select_related("application__artisan__user").filter(pk=pk).first())
        if not doc:
            raise Http404
        user = request.user
        is_admin = user.role == UserRole.ADMIN and user.is_staff
        if not (is_admin or doc.application.artisan.user_id == user.id):
            raise Http404
        if is_admin:
            audit.record(doc.application, "artisan_verification.document_viewed",
                         request=request, document_id=str(doc.id))
        ctype = mimetypes.guess_type(doc.file.name)[0] or "application/octet-stream"
        resp = FileResponse(doc.file.open("rb"), content_type=ctype)
        resp["Content-Disposition"] = f'inline; filename="{doc.original_name or "document"}"'
        resp["Cache-Control"] = "private, no-store"
        return resp


class AdminArtisanVerificationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AdminArtisanVerificationSerializer

    def get_queryset(self):
        qs = (ArtisanVerificationApplication.objects
              .select_related("artisan__user", "payment", "reviewed_by")
              .prefetch_related("categories", "documents"))
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        else:
            qs = qs.exclude(status=ArtisanVerificationStatus.DRAFT)
        return qs.order_by("submitted_at", "created_at")

    def _respond(self, app):
        app.refresh_from_db()
        return Response(AdminArtisanVerificationSerializer(app).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        app = self.get_object()
        review_application(app, admin=request.user, approve=True,
                           note=request.data.get("note", ""), request=request)
        return self._respond(app)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        app = self.get_object()
        review_application(app, admin=request.user, approve=False,
                           note=request.data.get("note", ""), request=request)
        return self._respond(app)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        app = self.get_object()
        note = request.data.get("note", "")
        if not note.strip():
            return Response({"note": "A reason is required."}, status=http.HTTP_400_BAD_REQUEST)
        revoke_artisan_verification(app, admin=request.user, note=note, request=request)
        return self._respond(app)
