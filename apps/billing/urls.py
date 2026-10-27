from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("billing/orders", views.BillOrderViewSet, basename="bill-order")

urlpatterns = [
    path("billing/catalogue/", views.CatalogueView.as_view(), name="billing-catalogue"),
    path("billing/variations/<str:kind>/", views.VariationsView.as_view(), name="billing-variations"),
    path("billing/verify-customer/", views.VerifyCustomerView.as_view(), name="billing-verify"),
    path("billing/wallet/", views.WalletView.as_view(), name="billing-wallet"),
    path("billing/wallet/fund/", views.WalletFundView.as_view(), name="billing-wallet-fund"),
    path("billing/webhook/vtu/", views.VTUWebhookView.as_view(), name="billing-vtu-webhook"),
    *router.urls,
]
