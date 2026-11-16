"""
Complaint box.

  POST /complaints/                         -> anyone (rate-limited); returns the reference
  GET  /complaints/mine/                    -> signed-in user's own complaints
  POST /complaints/track/                   -> {reference, email} -> status (no login needed)

  GET   /admin/complaints/?status=&category=&q=
  GET   /admin/complaints/<id>/
  PATCH /admin/complaints/<id>/              -> {status, admin_response, internal_note}
  GET   /admin/complaints/<id>/attachment/
"""
import mimetypes

from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.authentication import VersionedJWTAuthentication
from apps.common.permissions import IsAdmin

from . import services
from .models import Complaint, ComplaintStatus
from .serializers import (
    ComplaintAdminSerializer,
    ComplaintCreateSerializer,
    ComplaintPublicSerializer,
)


class ComplaintAnonThrottle(AnonRateThrottle):
    scope = "complaint"


class ComplaintUserThrottle(UserRateThrottle):
    scope = "complaint"


class _OptionalJWT(VersionedJWTAuthentication):
    """Public endpoint: attach the user if a valid token is sent, never reject without one."""

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Exception:  # noqa: BLE001 — expired/bad token -> treat as anonymous
            return None


class SubmitComplaintView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = [_OptionalJWT]
    throttle_classes = [ComplaintAnonThrottle, ComplaintUserThrottle]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        ser = ComplaintCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user if request.user and request.user.is_authenticated else None
        complaint = ser.save(user=user)
        services.on_submitted(complaint, request)
        return Response(ComplaintPublicSerializer(complaint).data, status=status.HTTP_201_CREATED)


class MyComplaintsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Complaint.objects.filter(Q(user=request.user) | Q(email__iexact=request.user.email))
        return Response(ComplaintPublicSerializer(qs[:50], many=True).data)


class TrackComplaintView(APIView):
    """Reference + the email it was filed with — both required, so references can't be enumerated."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ComplaintAnonThrottle]

    def post(self, request):
        ref = (request.data.get("reference") or "").strip().upper()
        email = (request.data.get("email") or "").strip()
        complaint = Complaint.objects.filter(reference=ref, email__iexact=email).first() if ref and email else None
        if not complaint:
            return Response({"detail": "No complaint matches that reference and email."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(ComplaintPublicSerializer(complaint).data)


class AdminComplaintViewSet(mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = ComplaintAdminSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        qs = Complaint.objects.select_related("artisan__user", "handled_by")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(Q(reference__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
                           | Q(full_name__icontains=q) | Q(subject__icontains=q)
                           | Q(transaction_reference__icontains=q))
        return qs.order_by("-created_at")

    def partial_update(self, request, *args, **kwargs):
        complaint = self.get_object()
        new_status = request.data.get("status")
        if new_status and new_status not in ComplaintStatus.values:
            return Response({"status": "Unknown status."}, status=status.HTTP_400_BAD_REQUEST)
        services.update_complaint(
            complaint, admin=request.user, status=new_status,
            response=request.data.get("admin_response"),
            internal_note=request.data.get("internal_note"), request=request)
        complaint.refresh_from_db()
        return Response(ComplaintAdminSerializer(complaint).data)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["get"],
            authentication_classes=[VersionedJWTAuthentication, SessionAuthentication])
    def attachment(self, request, pk=None):
        complaint = self.get_object()
        if not complaint.attachment:
            raise Http404
        ctype = mimetypes.guess_type(complaint.attachment.name)[0] or "application/octet-stream"
        resp = FileResponse(complaint.attachment.open("rb"), content_type=ctype)
        resp["Content-Disposition"] = "inline"
        resp["Cache-Control"] = "private, no-store"
        return resp
