"""
Booking API.

Access model:
  - create           -> customers only
  - list / retrieve  -> scoped: customers see their bookings, artisans see theirs,
                        admins see all (object ownership enforced via the queryset)
  - accept/start/complete -> the booking's artisan only
  - cancel           -> either party (customer or artisan) on the booking

Status is NEVER changed by a plain PATCH — only through the guarded actions below,
so the state machine can't be bypassed.
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import UserRole
from apps.common.permissions import IsArtisan, IsCustomer

from .models import Booking, BookingStatus
from .serializers import BookingCreateSerializer, BookingSerializer
from .services import transition_booking


class BookingViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]  # no PUT/PATCH/DELETE

    def get_serializer_class(self):
        return BookingCreateSerializer if self.action == "create" else BookingSerializer

    def get_permissions(self):
        if self.action == "create":
            return [IsCustomer()]
        if self.action in ("accept", "start", "complete"):
            return [IsArtisan()]
        return [IsAuthenticated()]  # list/retrieve/cancel (cancel checks party below)

    def get_queryset(self):
        user = self.request.user
        qs = Booking.objects.select_related(
            "customer__user", "artisan__user", "service"
        ).prefetch_related("history")
        if user.role == UserRole.CUSTOMER:
            return qs.filter(customer__user=user)
        if user.role == UserRole.ARTISAN:
            return qs.filter(artisan__user=user)
        if user.role == UserRole.ADMIN:
            return qs
        return qs.none()

    def get_queryset_with_status_filter(self):
        qs = self.get_queryset()
        status_param = self.request.query_params.get("status")
        return qs.filter(status=status_param) if status_param else qs

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset_with_status_filter())
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        booking = ser.save()
        return Response(
            BookingSerializer(booking, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    # ---- transition actions ----
    def _transition(self, request, to_status):
        booking = self.get_object()  # 404 if not in the caller's scope
        transition_booking(booking, to_status, actor=request.user)
        return Response(BookingSerializer(booking, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._transition(request, BookingStatus.ACCEPTED)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._transition(request, BookingStatus.IN_PROGRESS)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._transition(request, BookingStatus.COMPLETED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        # Either party may cancel; get_object already guarantees they're on the booking.
        return self._transition(request, BookingStatus.CANCELLED)
