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
  (card payments for bills and top-ups always go through BILLING_GATEWAY,
   Flutterwave by default)
  GET  /billing/orders/<id>/epin/<n>/       -> ePIN voucher pack image (owner only)
  POST /billing/webhook/vtu/<token>/        -> VTU.ng webhook (a trigger only: every
                                               event is re-checked with VTU.ng's API)
"""
import hmac
import json
import logging
import mimetypes

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserRole
from apps.accounts.throttles import OTPThrottle
from apps.payments.services import CheckoutError

from . import services
from .models import BillOrder, GiftCardTrade
from .serializers import (
    BillOrderCreateSerializer,
    BillOrderSerializer,
    GiftCardTradeCreateSerializer,
    GiftCardTradeSerializer,
    TopupSerializer,
    VerifyCustomerSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .vtu import VTUClient, VTUFailed, VTUUnknown, is_sandbox

logger = logging.getLogger(__name__)


class CatalogueView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({**services.catalogue(), "gateway": settings.BILLING_GATEWAY})


class VariationsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, kind):
        if kind not in ("data", "tv", "education"):
            return Response({"detail": "Unknown plan type."}, status=status.HTTP_404_NOT_FOUND)
        provider = (request.query_params.get("provider") or "").lower()
        service_type = request.query_params.get("service_type") or None
        allowed = {"data": services.DATA_NETWORKS, "tv": services.TV_PROVIDERS,
                   "education": services.EDUCATION_PROVIDERS}[kind]
        if provider not in allowed or (kind == "education" and (
                not settings.BILL_EDUCATION_ENABLED
                or service_type not in services.EDUCATION_SERVICE_TYPES)):
            return Response({"detail": "Choose a provider."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            items = services.plans(kind, provider, service_type)
        except (VTUUnknown, VTUFailed):
            return Response({"detail": "Plans are temporarily unavailable."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(items)


class EpinStockView(APIView):
    """GET /billing/epin-stock/ — packs in stock per network and value (empty if unknown)."""
    permission_classes = [AllowAny]

    def get(self, request):
        stock = services.epin_stock()
        return Response({"stock": {net: {str(k): v for k, v in row.items()} for net, row in stock.items()},
                         "known": bool(stock)})


class VerifyCustomerView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [OTPThrottle]   # lookups are enumerable data — keep them slow

    def post(self, request):
        ser = VerifyCustomerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        info = services.verify_customer(service=d["service"], provider=d["provider"],
                                        customer_id=d["customer_id"],
                                        meter_type=d.get("meter_type"),
                                        variation_code=d.get("variation_id"))
        return Response({k: v for k, v in info.items() if v not in (None, "")})


def _vtu_list(fetch):
    try:
        return Response(fetch())
    except (VTUUnknown, VTUFailed):
        return Response({"detail": "This service is temporarily unavailable."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)


class GiftCardCatalogView(APIView):
    """GET countries | products?country_code= | sell-catalog | sell-rates?slug="""

    permission_classes = [IsAuthenticated]

    def get(self, request, what):
        q = request.query_params
        client = VTUClient()
        if what in ("countries", "products"):
            if not settings.BILL_GIFTCARD_BUY_ENABLED:
                return Response({"detail": "Not available."}, status=status.HTTP_404_NOT_FOUND)
            if what == "countries":
                return _vtu_list(client.giftcard_countries)
            return _vtu_list(lambda: client.giftcard_products((q.get("country_code") or "US").upper()[:2]))
        if what in ("sell-catalog", "sell-rates"):
            if not settings.BILL_GIFTCARD_SELL_ENABLED:
                return Response({"detail": "Not available."}, status=status.HTTP_404_NOT_FOUND)
            if what == "sell-catalog":
                return _vtu_list(client.giftcard_sell_catalog)
            resp = _vtu_list(lambda: client.giftcard_sell_rates(q.get("slug") or ""))
            if resp.status_code == 200:
                resp.data = {"rates": resp.data,
                             "jobcatch_margin_percent": str(settings.GIFTCARD_SELL_MARGIN_PERCENT)}
            return resp
        return Response({"detail": "Unknown."}, status=status.HTTP_404_NOT_FOUND)


class GiftCardQuoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        q = services.giftcard_quote(product_id=request.data.get("product_id"),
                                    amount=request.data.get("amount"),
                                    quantity=request.data.get("quantity"))
        return Response({"price_ngn": str(q["price_ngn"]), "quantity": q["quantity"]})


class RmbView(APIView):
    """GET rates/limits for the form; POST a quote for {channel, account_type, rmb_amount}."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.BILL_RMB_ENABLED:
            return Response({"detail": "Not available."}, status=status.HTTP_404_NOT_FOUND)
        try:
            data = VTUClient().rmb_rates()
        except (VTUUnknown, VTUFailed):
            return Response({"detail": "RMB rates are temporarily unavailable."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        channels = [{"channel": c.get("channel"), "name": c.get("name"),
                     "is_available": c.get("is_available", True),
                     "account_types": [{"type": a.get("type"), "name": a.get("name"),
                                        "is_available": a.get("is_available", True)}
                                       for a in c.get("account_types") or []]}
                    for c in data.get("channels") or [] if c.get("channel") in services.RMB_CHANNELS]
        return Response({"channels": channels, "limits": data.get("limits") or {}})

    def post(self, request):
        q = services.rmb_quote(channel=request.data.get("channel"),
                               account_type=request.data.get("account_type"),
                               rmb_amount=request.data.get("rmb_amount"))
        return Response({"rmb_amount": str(q["rmb_amount"]), "rate": str(q["customer_rate"]),
                         "price_ngn": str(q["price_ngn"])})


class GiftCardTradeViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """Selling gift cards: submit (multipart for physical-card photos) and track."""

    permission_classes = [IsAuthenticated]
    serializer_class = GiftCardTradeSerializer

    def get_queryset(self):
        return GiftCardTrade.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        ser = GiftCardTradeCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        trade = services.submit_giftcard_trade(user=request.user, data=ser.validated_data,
                                               images=request.FILES.getlist("images"))
        return Response(GiftCardTradeSerializer(trade).data, status=status.HTTP_201_CREATED)


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
        try:
            order, url = services.create_order(user=request.user, data=data,
                                               payment_method=method)
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

    @action(detail=True, methods=["get"], url_path=r"epin/(?P<index>\d+)")
    def epin(self, request, pk=None, index=None):
        """One delivered ePIN pack as an image. Only the buyer (or staff) can open it."""
        from apps.common.storage import private_storage

        order = self.get_object()
        packs = [p for p in (order.epins or []) if isinstance(p, dict) and p.get("file")]
        i = int(index)
        if i >= len(packs):
            raise Http404
        name = packs[i]["file"]
        resp = FileResponse(private_storage().open(name, "rb"),
                            content_type=mimetypes.guess_type(name)[0] or "image/webp")
        resp["Content-Disposition"] = f'inline; filename="jobcatch-pins-{order.receipt_number}-{i + 1}.webp"'
        resp["Cache-Control"] = "private, no-store"
        return resp


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
                user=request.user, amount=ser.validated_data["amount"])
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"authorization_url": url, "reference": payment.reference},
                        status=status.HTTP_201_CREATED)


class VTUWebhookView(APIView):
    """
    VTU.ng's Partner API doesn't sign webhooks, so a webhook is only a trigger:
    the URL carries a secret token, and the order is moved only by what
    GET /transactions/{reference} (authenticated with our API key) says.
    The sandbox doesn't store transactions, so there the event itself is used.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request, token=""):
        expected = settings.VTU_WEBHOOK_TOKEN
        if not expected or not hmac.compare_digest(str(token), expected):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return Response({"detail": "Bad payload."}, status=status.HTTP_400_BAD_REQUEST)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if bool(payload.get("livemode")) == is_sandbox():
            logger.warning("VTU webhook %s ignored: livemode does not match this environment",
                           payload.get("id"))
            return Response({"status": "ignored"})
        reference = data.get("reference") or data.get("transaction_reference")
        order = BillOrder.objects.filter(provider_order_id=reference).first() if reference else None
        trade = (GiftCardTrade.objects.filter(provider_reference=reference).first()
                 if reference and not order else None)
        if trade:
            return self._trade(trade, reference, data)
        if not order:
            logger.warning("VTU webhook %s for unknown reference %s", payload.get("event"), reference)
            return Response({"status": "ignored"})
        if is_sandbox():
            status_value = data.get("transaction_status") or data.get("status") or ""
            services.apply_biller_status(order, {**data, "reference": reference,
                                                 "status": status_value}, source="webhook")
        else:
            try:
                result = VTUClient().get_transaction(reference=reference)
            except (VTUFailed, VTUUnknown) as exc:
                logger.warning("VTU webhook %s: lookup failed (%s); requery will retry",
                               reference, exc)
                return Response({"status": "retry"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            services.apply_biller_status(order, result.transaction, source="lookup")
        return Response({"status": "success"})

    def _trade(self, trade, reference, data):
        if is_sandbox():
            services.apply_trade_status(trade, {**data, "reference": reference,
                                                "status": data.get("status") or ""},
                                        source="webhook")
            return Response({"status": "success"})
        try:
            result = VTUClient().get_transaction(reference=reference)
        except (VTUFailed, VTUUnknown):
            return Response({"status": "retry"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        services.apply_trade_status(trade, result.transaction, source="lookup")
        return Response({"status": "success"})
