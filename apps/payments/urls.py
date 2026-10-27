from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    FlutterwaveWebhookView,
    GatewayListView,
    InitializePaymentView,
    PaymentViewSet,
    PaystackWebhookView,
    ReceiptView,
    VerifyPaymentView,
)

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [
    # Explicit routes MUST precede the router's /payments/<pk>/ detail route.
    path("payments/gateways/", GatewayListView.as_view(), name="payment-gateways"),
    path("payments/initialize/", InitializePaymentView.as_view(), name="payment-initialize"),
    path("payments/verify/<str:reference>/", VerifyPaymentView.as_view(), name="payment-verify"),
    path("payments/receipt/<str:reference>/", ReceiptView.as_view(), name="payment-receipt"),
    path("payments/webhook/paystack/", PaystackWebhookView.as_view(), name="paystack-webhook"),
    path("payments/webhook/flutterwave/", FlutterwaveWebhookView.as_view(),
         name="flutterwave-webhook"),
    *router.urls,
]
