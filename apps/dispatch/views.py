"""
Dispatch & delivery API.

  GET  /dispatch/rates/                          -> per-vehicle pricing (public)
  POST /dispatch/quote/                          -> fare for pickup/dropoff/vehicle/kind
  GET|POST /dispatch/deliveries/                 -> my deliveries / book one
  GET  /dispatch/deliveries/<id>/                -> customer or assigned rider
  POST /dispatch/deliveries/<id>/cancel|pay|rate/
  GET|PATCH /dispatch/rider/                     -> my rider profile (multipart for the ID)
  GET  /dispatch/banks/                          -> banks for the payout account
  POST /dispatch/rider/payout-account/           -> verify + register the split subaccount
  POST /dispatch/rider/availability/             -> {online, lat, lng}
  GET  /dispatch/rider/requests/                 -> open requests near me
  GET  /dispatch/rider/jobs/                     -> my jobs + earnings
  POST /dispatch/rider/jobs/<id>/accept|release|picked-up|deliver/
  /admin/riders/ ...                             -> review riders (admins)
"""
import mimetypes

from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import VersionedJWTAuthentication
from apps.common.permissions import IsAdmin

from . import services
from .models import Delivery, DeliveryRate, DeliveryStatus, RiderProfile, RiderStatus
from .serializers import (
    AdminRiderSerializer, DeliveryCreateSerializer, DeliverySerializer, OpenRequestSerializer,
    QuoteSerializer, RateSerializer, RiderProfileSerializer,
)


class RatesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings

        return Response({"rates": RateSerializer(DeliveryRate.objects.filter(is_active=True),
                                                 many=True).data,
                         "rider_share_percent": str(settings.DISPATCH_RIDER_SHARE_PERCENT)})


class QuoteView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = QuoteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        q = services.quote(pickup=services.point(d["pickup_lat"], d["pickup_lng"]),
                           dropoff=services.point(d["dropoff_lat"], d["dropoff_lng"]),
                           vehicle_type=d["vehicle_type"], kind=d["kind"])
        return Response({k: str(v) for k, v in q.items()})


class DeliveryViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DeliverySerializer

    def get_queryset(self):
        u = self.request.user
        qs = Delivery.objects.select_related("rider__user", "customer", "payment")
        if self.action == "list":
            return qs.filter(customer=u)
        return qs.filter(Q(customer=u) | Q(rider__user=u))

    def create(self, request, *args, **kwargs):
        ser = DeliveryCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        delivery = services.create_delivery(customer=request.user, data=ser.validated_data)
        return Response(DeliverySerializer(delivery, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    def _out(self, d):
        d.refresh_from_db()
        return Response(DeliverySerializer(d, context={"request": self.request}).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._out(services.cancel(self.get_object(), request.user,
                                         str(request.data.get("reason") or "")))

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        url = services.start_payment(self.get_object(), request.user)
        return Response({"authorization_url": url})

    @action(detail=True, methods=["post"])
    def rate(self, request, pk=None):
        return self._out(services.rate(self.get_object(), request.user,
                                       request.data.get("rating") or 0,
                                       str(request.data.get("review") or "")))


class RiderProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        return Response(RiderProfileSerializer(services.rider_for(request.user)).data)

    def patch(self, request):
        rider = services.rider_for(request.user)
        ser = RiderProfileSerializer(rider, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        doc = request.FILES.get("id_document")
        if doc and (doc.content_type not in ("image/jpeg", "image/png", "application/pdf")
                    or doc.size > 5 * 1024 * 1024):
            return Response({"id_document": "Upload a JPEG, PNG or PDF up to 5 MB."},
                            status=status.HTTP_400_BAD_REQUEST)
        selfie = request.FILES.get("selfie")
        if selfie and (selfie.content_type not in ("image/jpeg", "image/png", "image/webp", "image/heic")
                       or selfie.size > 8 * 1024 * 1024):
            return Response({"selfie": "Take a clear photo of your face (JPEG or PNG, up to 8 MB)."},
                            status=status.HTTP_400_BAD_REQUEST)
        rider = services.update_rider(rider, ser.validated_data, id_document=doc, selfie=selfie)
        return Response(RiderProfileSerializer(rider).data)


class BanksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(services.banks())


class PayoutAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rider = services.set_payout_account(
            services.rider_for(request.user), bank_code=str(request.data.get("bank_code") or ""),
            account_number=str(request.data.get("account_number") or "").strip(),
            bank_name=str(request.data.get("bank_name") or ""))
        return Response(RiderProfileSerializer(rider).data)


class AvailabilityView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rider = services.set_availability(
            services.rider_for(request.user), online=bool(request.data.get("online")),
            lat=request.data.get("lat"), lng=request.data.get("lng"))
        return Response(RiderProfileSerializer(rider).data)


class OpenRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rider = services.rider_for(request.user)
        return Response(OpenRequestSerializer(services.open_requests(rider), many=True,
                                              context={"request": request}).data)


class RiderJobViewSet(viewsets.ReadOnlyModelViewSet):
    """The rider's accepted jobs (active and history) and the actions on them."""

    permission_classes = [IsAuthenticated]
    serializer_class = DeliverySerializer

    def get_queryset(self):
        return Delivery.objects.select_related("rider__user", "customer").filter(
            rider__user=self.request.user)

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        done = qs.filter(status=DeliveryStatus.DELIVERED)
        return Response({
            "jobs": DeliverySerializer(qs[:50], many=True, context={"request": request}).data,
            "earnings": {"total": str(done.aggregate(s=Sum("rider_share"))["s"] or 0),
                         "count": done.count()},
        })

    def _rider(self):
        return services.rider_for(self.request.user)

    def _out(self, d):
        d.refresh_from_db()
        return Response(DeliverySerializer(d, context={"request": self.request}).data)

    def _open(self, pk):
        d = Delivery.objects.filter(pk=pk).first()
        if not d:
            raise Http404
        return d

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._out(services.accept(self._open(pk), self._rider()))

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        return self._out(services.release(self.get_object(), self._rider()))

    @action(detail=True, methods=["post"], url_path="picked-up")
    def picked_up(self, request, pk=None):
        return self._out(services.mark_picked_up(self.get_object(), self._rider()))

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        return self._out(services.deliver(self.get_object(), self._rider(),
                                          request.data.get("code") or ""))


class AdminRiderViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AdminRiderSerializer

    def get_queryset(self):
        qs = RiderProfile.objects.select_related("user").order_by("-updated_at")
        s = self.request.query_params.get("status")
        return qs.filter(status=s) if s else qs.exclude(status=RiderStatus.INCOMPLETE)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        rider = services.review_rider(self.get_object(), admin=request.user, approve=True,
                                      note=str(request.data.get("note") or ""))
        return Response(AdminRiderSerializer(rider).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        note = str(request.data.get("note") or "").strip()
        if not note:
            return Response({"note": "Tell the rider what to fix."}, status=status.HTTP_400_BAD_REQUEST)
        rider = services.review_rider(self.get_object(), admin=request.user, approve=False, note=note)
        return Response(AdminRiderSerializer(rider).data)

    @staticmethod
    def _file(f):
        if not f:
            raise Http404
        resp = FileResponse(f.open("rb"), content_type=mimetypes.guess_type(f.name)[0]
                            or "application/octet-stream")
        resp["Content-Disposition"] = "inline"
        resp["Cache-Control"] = "private, no-store"
        return resp

    @action(detail=True, methods=["get"], url_path="id-document",
            authentication_classes=[VersionedJWTAuthentication, SessionAuthentication])
    def id_document(self, request, pk=None):
        return self._file(self.get_object().id_document)

    @action(detail=True, methods=["get"], url_path="selfie",
            authentication_classes=[VersionedJWTAuthentication, SessionAuthentication])
    def selfie(self, request, pk=None):
        return self._file(self.get_object().selfie)
