"""
Employer KYC API.

Employer (own case):
  GET/PATCH /kyc/employer/                    -> details, checklist, documents
  POST      /kyc/employer/otp/send/           -> {channel: email|sms}
  POST      /kyc/employer/otp/verify/         -> {channel, code}
  POST      /kyc/employer/documents/          -> multipart {doc_type, file}
  DELETE    /kyc/employer/documents/<id>/
  POST      /kyc/employer/submit/             -> run lookups, move to review

Shared:
  GET /kyc/documents/<id>/download/           -> owner or admin only (private file)

Admin review dashboard:
  GET  /admin/kyc/?status=pending&q=          -> queue
  GET  /admin/kyc/<id>/                       -> full case incl. audit history
  POST /admin/kyc/<id>/approve/               -> {note, override}
  POST /admin/kyc/<id>/reject/                -> {reason}
  POST /admin/kyc/<id>/revoke/                -> {reason}
  POST /admin/kyc/<id>/rerun-checks/
  POST /admin/kyc/documents/<id>/review/      -> {status: accepted|rejected, note}
"""
import mimetypes

from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import VersionedJWTAuthentication
from apps.accounts.throttles import OTPThrottle
from apps.common import audit
from apps.common.permissions import IsAdmin, IsEmployer

from . import services
from .models import DocumentStatus, EmployerKYC, KYCDocument, KYCStatus
from .serializers import (
    AdminKYCDetailSerializer,
    AdminKYCListSerializer,
    EmployerKYCSerializer,
    KYCDocumentSerializer,
)


def _is_admin(user):
    from apps.accounts.models import UserRole

    return bool(user and user.is_authenticated and user.role == UserRole.ADMIN and user.is_staff)


class MyKYCView(APIView):
    permission_classes = [IsEmployer]

    def _kyc(self, request):
        return services.kyc_for(request.user.employer_profile)

    def get(self, request):
        return Response(EmployerKYCSerializer(self._kyc(request)).data)

    def patch(self, request):
        kyc = self._kyc(request)
        services._require_editable(kyc)
        ser = EmployerKYCSerializer(kyc, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        services.mark_in_progress(kyc, request)
        audit.record(kyc, "kyc.details_updated", request=request,
                     fields=sorted(k for k in request.data.keys() if k != "director_nin")
                     + (["director_nin"] if "director_nin" in request.data else []))
        kyc.refresh_from_db()
        return Response(EmployerKYCSerializer(kyc).data)


class SendOTPView(APIView):
    permission_classes = [IsEmployer]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        kyc = services.kyc_for(request.user.employer_profile)
        services.send_contact_otp(kyc, request.data.get("channel"), request)
        return Response({"detail": "Code sent."})


class VerifyOTPView(APIView):
    permission_classes = [IsEmployer]
    throttle_classes = [OTPThrottle]

    def post(self, request):
        kyc = services.kyc_for(request.user.employer_profile)
        channel = request.data.get("channel")
        if channel not in ("email", "sms"):
            return Response({"channel": "Choose email or sms."}, status=status.HTTP_400_BAD_REQUEST)
        services.verify_contact_otp(kyc, channel, request.data.get("code", ""), request)
        kyc.refresh_from_db()
        return Response(EmployerKYCSerializer(kyc).data)


class SubmitKYCView(APIView):
    permission_classes = [IsEmployer]

    def post(self, request):
        kyc = services.kyc_for(request.user.employer_profile)
        kyc = services.submit(kyc, request)
        return Response(EmployerKYCSerializer(kyc).data)


class MyKYCDocumentViewSet(mixins.CreateModelMixin, mixins.DestroyModelMixin,
                           mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsEmployer]
    serializer_class = KYCDocumentSerializer

    def get_queryset(self):
        return KYCDocument.objects.filter(kyc__employer__user=self.request.user)

    def perform_create(self, serializer):
        kyc = services.kyc_for(self.request.user.employer_profile)
        services._require_editable(kyc)
        doc = serializer.save(kyc=kyc)
        services.mark_in_progress(kyc, self.request)
        audit.record(kyc, "kyc.document_uploaded", request=self.request,
                     doc_type=doc.doc_type, document_id=str(doc.id))

    def perform_destroy(self, instance):
        kyc = instance.kyc
        services._require_editable(kyc)
        audit.record(kyc, "kyc.document_deleted", request=self.request,
                     doc_type=instance.doc_type, document_id=str(instance.id))
        instance.file.delete(save=False)
        instance.delete()


class DocumentDownloadView(APIView):
    """Stream a private KYC document to its owner or an admin. Never a public URL."""

    permission_classes = [IsAuthenticated]
    # JWT for the web app; session so the Django admin's "Open" links work too.
    authentication_classes = [VersionedJWTAuthentication, SessionAuthentication]

    def get(self, request, pk):
        doc = KYCDocument.objects.select_related("kyc__employer__user").filter(pk=pk).first()
        if not doc:
            raise Http404
        is_admin = _is_admin(request.user)
        if not (is_admin or doc.kyc.employer.user_id == request.user.id):
            raise Http404   # don't reveal existence
        if is_admin:
            audit.record(doc.kyc, "kyc.document_viewed", request=request,
                         document_id=str(doc.id), doc_type=doc.doc_type)
        content_type = mimetypes.guess_type(doc.file.name)[0] or "application/octet-stream"
        response = FileResponse(doc.file.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{doc.original_name or "document"}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


# ------------------------------------------------------------------ admin dashboard
class AdminKYCViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]

    def get_serializer_class(self):
        return AdminKYCDetailSerializer if self.action != "list" else AdminKYCListSerializer

    def get_queryset(self):
        qs = (EmployerKYC.objects.select_related("employer__user", "reviewed_by")
              .prefetch_related("documents"))
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        else:
            qs = qs.exclude(status=KYCStatus.NOT_STARTED)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(registered_name__icontains=q) | Q(cac_number__icontains=q)
                           | Q(employer__company_name__icontains=q)
                           | Q(employer__user__email__icontains=q))
        # Oldest submissions first so nobody waits in the queue forever.
        return qs.order_by("submitted_at", "created_at")

    def retrieve(self, request, *args, **kwargs):
        kyc = self.get_object()
        audit.record(kyc, "kyc.case_viewed", request=request)
        return Response(AdminKYCDetailSerializer(kyc).data)

    def _detail(self, kyc):
        kyc.refresh_from_db()
        return Response(AdminKYCDetailSerializer(kyc).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        kyc = self.get_object()
        services.approve(kyc, actor=request.user, note=request.data.get("note", ""),
                         request=request, override=bool(request.data.get("override")))
        return self._detail(kyc)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        kyc = self.get_object()
        services.reject(kyc, request.user, request.data.get("reason", ""), request=request)
        return self._detail(kyc)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        kyc = self.get_object()
        services.reject(kyc, request.user, request.data.get("reason", ""),
                        request=request, revoke=True)
        return self._detail(kyc)

    @action(detail=True, methods=["post"], url_path="rerun-checks")
    def rerun_checks(self, request, pk=None):
        kyc = self.get_object()
        services.run_automated_checks(kyc, actor=request.user, request=request)
        return self._detail(kyc)


class AdminDocumentReviewView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        doc = KYCDocument.objects.select_related("kyc").filter(pk=pk).first()
        if not doc:
            raise Http404
        new_status = request.data.get("status")
        if new_status not in (DocumentStatus.ACCEPTED, DocumentStatus.REJECTED):
            return Response({"status": "Use accepted or rejected."},
                            status=status.HTTP_400_BAD_REQUEST)
        doc.status = new_status
        doc.review_note = (request.data.get("note") or "")[:255]
        doc.save(update_fields=["status", "review_note", "updated_at"])
        audit.record(doc.kyc, f"kyc.document_{new_status}", request=request,
                     document_id=str(doc.id), doc_type=doc.doc_type, note=doc.review_note)
        return Response(AdminKYCDetailSerializer(doc.kyc).data)
