"""
Payments API.

  GET  /payments/gateways/             -> which checkouts are available
  POST /payments/initialize/           -> booking / subscription / CV service checkout
  GET  /payments/verify/<ref>/         -> confirm a payment (frontend fallback)
  POST /payments/webhook/paystack/     -> Paystack callback (HMAC-SHA512 signed)
  POST /payments/webhook/flutterwave/  -> Flutterwave callback (verif-hash, re-verified)
  GET  /payments/                      -> transaction history (own; admin sees all)
  GET  /payments/receipt/<ref>/        -> printable receipt / invoice
"""
import json
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserRole
from apps.subscriptions.models import Subscription

from .gateways import GatewayError, available_gateways, get_gateway
from .models import Payment, PaymentEventKind, PaymentPurpose, PaymentStatus
from .serializers import PaymentInitializeSerializer, PaymentSerializer, ReceiptSerializer
from .services import CheckoutError, log_event, start_checkout, verify_with_gateway
from .tasks import process_successful_payment

logger = logging.getLogger(__name__)


def checkout_response(payment, authorization_url):
    return Response(
        {"authorization_url": authorization_url, "reference": payment.reference,
         "invoice_number": payment.invoice_number, "amount": str(payment.amount),
         "gateway": payment.gateway},
        status=status.HTTP_201_CREATED,
    )


def checkout_error_response(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class GatewayListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"default": settings.DEFAULT_PAYMENT_GATEWAY,
                         "available": available_gateways()})


class InitializePaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PaymentInitializeSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        purpose = ser.validated_data["purpose"]
        booking = None

        if purpose == PaymentPurpose.BOOKING:
            booking = ser.validated_data["booking"]
            amount = booking.agreed_price
            description = f"Booking payment — {booking.service.title}"
            link_subscription = None
        elif purpose == PaymentPurpose.CV_SERVICE:
            amount = settings.CV_SERVICE_PRICE
            description = "JobCatch CV Service (one-off)"
            link_subscription = None
        else:
            plan = ser.validated_data["plan"]
            amount = settings.SUBSCRIPTION_PRICES[plan]
            description = f"{plan.title()} plan — {settings.SUBSCRIPTION_PERIOD_DAYS} days"

            def link_subscription(payment):
                # Created inactive; activated on confirmed payment.
                Subscription.objects.create(
                    user=request.user, plan=plan, is_active=False,
                    started_at=timezone.now(), payment=payment,
                )

        try:
            payment, url = start_checkout(
                user=request.user, purpose=purpose, amount=amount, description=description,
                gateway=ser.validated_data["gateway"], booking=booking,
                before_gateway=link_subscription,
            )
        except CheckoutError as exc:
            return checkout_error_response(exc)
        return checkout_response(payment, url)


class _WebhookView(APIView):
    """Gateway-to-server callback. Public, but authenticated before trust."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []
    gateway_name = None

    def post(self, request, *args, **kwargs):
        gateway = get_gateway(self.gateway_name)
        if not gateway.verify_webhook(request):
            return Response({"detail": "Invalid signature."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return Response({"detail": "Bad payload."}, status=status.HTTP_400_BAD_REQUEST)
        reference, pstatus, amount = gateway.parse_event(payload)
        payment = Payment.objects.filter(reference=reference).first() if reference else None
        if payment:
            log_event(payment, PaymentEventKind.WEBHOOK, source=f"webhook:{self.gateway_name}",
                      note=str(payload.get("event", ""))[:100])
            self.handle(payment, pstatus, amount)
        return Response({"detail": "received"})

    def handle(self, payment, pstatus, amount):
        raise NotImplementedError


class PaystackWebhookView(_WebhookView):
    gateway_name = "paystack"

    def handle(self, payment, pstatus, amount):
        # Paystack payloads are HMAC-signed with our secret, so they're trusted.
        if pstatus == "success":
            process_successful_payment.delay(
                payment.reference, str(amount) if amount is not None else None,
                "webhook:paystack",
            )


class FlutterwaveWebhookView(_WebhookView):
    gateway_name = "flutterwave"

    def handle(self, payment, pstatus, amount):
        # The verif-hash is a static shared secret, so the body is only a hint:
        # value is delivered after re-verifying the transaction with Flutterwave.
        try:
            verify_with_gateway(payment, source="webhook:flutterwave")
        except GatewayError as exc:
            logger.warning("Flutterwave re-verify failed for %s: %s", payment.reference, exc)


class VerifyPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        payment = Payment.objects.filter(reference=reference, payer=request.user).first()
        if not payment:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if payment.status == PaymentStatus.INITIATED or not payment.fulfilled_at:
            try:
                payment = verify_with_gateway(payment, source="verify")
            except GatewayError as exc:
                logger.warning("Verify failed for %s: %s", reference, exc)
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)


class ReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        qs = Payment.objects.select_related("payer").prefetch_related("events")
        if not (request.user.role == UserRole.ADMIN and request.user.is_staff):
            qs = qs.filter(payer=request.user)
        payment = qs.filter(reference=reference).first()
        if not payment:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReceiptSerializer(payment).data)


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """Transaction history. ?purpose= and ?status= filter."""

    serializer_class = PaymentSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Payment.objects.all() if user.role == UserRole.ADMIN else Payment.objects.filter(payer=user)
        for field in ("purpose", "status"):
            value = self.request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        return qs
