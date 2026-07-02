from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    InitializePaymentView,
    PaymentViewSet,
    PaystackWebhookView,
    VerifyPaymentView,
)

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [
    # Explicit routes MUST precede the router's /payments/<pk>/ detail route.
    path("payments/initialize/", InitializePaymentView.as_view(), name="payment-initialize"),
    path("payments/verify/<str:reference>/", VerifyPaymentView.as_view(), name="payment-verify"),
    path("payments/webhook/paystack/", PaystackWebhookView.as_view(), name="paystack-webhook"),
    *router.urls,
]
