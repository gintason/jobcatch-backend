"""
Payments API.

  POST /payments/initialize/        -> create a Payment + gateway auth URL
  GET  /payments/verify/<ref>/      -> confirm a payment (frontend fallback)
  POST /payments/webhook/paystack/  -> gateway callback (signature-verified)
  GET  /payments/                   -> transaction history (own; admin sees all)
"""
import json
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserRole
from apps.subscriptions.models import Subscription

from .gateways import get_gateway
from .models import Payment, PaymentPurpose, PaymentStatus
from .serializers import PaymentInitializeSerializer, PaymentSerializer
from .services import compute_commission
from .tasks import process_successful_payment

GATEWAY = "paystack"


class InitializePaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PaymentInitializeSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        purpose = ser.validated_data["purpose"]
        reference = "JC-" + uuid4().hex[:20]

        if purpose == PaymentPurpose.BOOKING:
            booking = ser.validated_data["booking"]
            amount = booking.agreed_price
        else:
            plan = ser.validated_data["plan"]
            booking = None
            amount = settings.SUBSCRIPTION_PRICES[plan]

        payment = Payment.objects.create(
            payer=request.user, purpose=purpose, booking=booking, gateway=GATEWAY,
            reference=reference, amount=amount,
            commission=compute_commission(purpose, amount),
            status=PaymentStatus.INITIATED,
        )
        if purpose == PaymentPurpose.SUBSCRIPTION:
            # Created inactive; activated on webhook success.
            Subscription.objects.create(
                user=request.user, plan=ser.validated_data["plan"],
                is_active=False, started_at=timezone.now(), payment=payment,
            )

        result = get_gateway(GATEWAY).initialize(
            reference=reference, amount_kobo=int(amount * 100),
            email=request.user.email, callback_url=settings.PAYMENT_CALLBACK_URL,
        )
        return Response(
            {"authorization_url": result["authorization_url"], "reference": reference},
            status=status.HTTP_201_CREATED,
        )


class PaystackWebhookView(APIView):
    """Gateway-to-server callback. Public, but signature-verified before trust."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        gateway = get_gateway(GATEWAY)
        raw = request.body
        signature = request.META.get("HTTP_X_PAYSTACK_SIGNATURE", "")
        if not gateway.verify_signature(raw, signature):
            return Response({"detail": "Invalid signature."},
                            status=status.HTTP_400_BAD_REQUEST)
        payload = json.loads(raw or b"{}")
        reference, pstatus = gateway.parse_event(payload)
        if pstatus == "success" and reference:
            process_successful_payment.delay(reference)   # async fulfilment
        return Response({"detail": "received"})


class VerifyPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        payment = Payment.objects.filter(reference=reference, payer=request.user).first()
        if not payment:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        result = get_gateway(GATEWAY).verify(reference)
        if result["status"] == "success":
            process_successful_payment(reference)   # sync confirm
            payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    """Transaction history."""

    serializer_class = PaymentSerializer

    def get_queryset(self):
        user = self.request.user
        if user.role == UserRole.ADMIN:
            return Payment.objects.all()
        return Payment.objects.filter(payer=user)
