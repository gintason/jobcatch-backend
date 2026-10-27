"""
Bill payments & wallet.

  GET  /billing/catalogue/                  -> networks, discos, TV providers, fees
  GET  /billing/variations/<data|tv>/?provider=  -> plans/bouquets with prices (public)
  POST /billing/verify-customer/            -> meter / smartcard lookup (name, arrears)
  POST /billing/orders/                     -> buy (wallet or card); card returns checkout URL
  GET  /billing/orders/[<id>/]              -> history / receipt
  POST /billing/orders/<id>/requery/        -> refresh a processing order
  GET  /billing/wallet/                     -> balance + ledger
  POST /billing/wallet/fund/                -> top up by card/bank transfer
  POST /billing/webhook/vtu/                -> VTU.ng status webhook (HMAC-SHA256 with PIN)
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserRole
from apps.accounts.throttles import OTPThrottle
from apps.payments.services import CheckoutError

from . import services
from .models import BillOrder
from .serializers import (
    BillOrderCreateSerializer,
    BillOrderSerializer,
    TopupSerializer,
    VerifyCustomerSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .vtu import VTUClient, VTUUnknown

logger = logging.getLogger(__name__)


class CatalogueView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(services.catalogue())


class VariationsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, kind):
        if kind not in ("data", "tv"):
            return Response({"detail": "Unknown plan type."}, status=status.HTTP_404_NOT_FOUND)
        provider = request.query_params.get("provider") or None
        try:
            items = VTUClient().variations(kind, provider)
        except VTUUnknown:
            return Response({"detail": "Plans are temporarily unavailable."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response([i for i in items
                         if str(i.get("availability", "Available")).lower() == "available"])


class VerifyCustomerView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [OTPThrottle]   # lookups are enumerable data — keep them slow

    def post(self, request):
        ser = VerifyCustomerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        info = services.verify_customer(service=d["service"], provider=d["provider"],
                                        customer_id=d["customer_id"],
                                        meter_type=d.get("meter_type"))
        keep = ("customer_name", "customer_address", "customer_arrears", "outstanding",
                "min_purchase_amount", "max_purchase_amount", "status", "due_date",
                "current_bouquet", "renewal_amount", "service_name", "customer_account_type")
        return Response({k: info.get(k) for k in keep if k in info})


class BillOrderViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BillOrderSerializer

    def get_queryset(self):
        user = self.request.user
        qs = BillOrder.objects.select_related("payment")
        if not (user.role == UserRole.ADMIN and user.is_staff):
            qs = qs.filter(user=user)
        service = self.request.query_params.get("service")
        return qs.filter(service=service) if service else qs

    def create(self, request, *args, **kwargs):
        ser = BillOrderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        method = data.pop("payment_method")
        gateway = data.pop("gateway", None)
        try:
            order, url = services.create_order(user=request.user, data=data,
                                               payment_method=method, gateway=gateway)
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        body = BillOrderSerializer(order).data
        body["authorization_url"] = url
        return Response(body, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def requery(self, request, pk=None):
        order = services.requery_order(self.get_object())
        order.refresh_from_db()
        return Response(BillOrderSerializer(order).data)


class WalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet = services.wallet_for(request.user)
        txns = wallet.transactions.all()[:50]
        return Response({**WalletSerializer(wallet).data,
                         "transactions": WalletTransactionSerializer(txns, many=True).data})


class WalletFundView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = TopupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            payment, url = services.start_wallet_topup(
                user=request.user, amount=ser.validated_data["amount"],
                gateway=ser.validated_data.get("gateway"))
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"authorization_url": url, "reference": payment.reference},
                        status=status.HTTP_201_CREATED)


def _vtu_signature_ok(raw, signature):
    pin = settings.VTU_USER_PIN
    if not (pin and signature):
        return False
    candidates = [raw]
    try:
        # VTU.ng's sample signs json.dumps(payload, separators=(",", ":")) —
        # accept that serialisation as well as the raw body.
        candidates.append(json.dumps(json.loads(raw), separators=(",", ":")).encode())
    except ValueError:
        pass
    for body in candidates:
        computed = hmac.new(pin.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(computed, signature):
            return True
    return False


class VTUWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        raw = request.body or b"{}"
        if not _vtu_signature_ok(raw, request.META.get("HTTP_X_SIGNATURE", "")):
            return Response({"detail": "Invalid signature."}, status=status.HTTP_403_FORBIDDEN)
        try:
            payload = json.loads(raw)
        except ValueError:
            return Response({"detail": "Bad payload."}, status=status.HTTP_400_BAD_REQUEST)
        order = BillOrder.objects.filter(request_id=payload.get("request_id")).first()
        if order:
            services.apply_biller_status(order, payload, source="webhook")
        else:
            logger.warning("VTU webhook for unknown request_id %s", payload.get("request_id"))
        return Response({"status": "success"})
