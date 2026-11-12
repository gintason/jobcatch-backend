from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("billing/orders", views.BillOrderViewSet, basename="bill-order")
router.register("billing/giftcards/trades", views.GiftCardTradeViewSet, basename="giftcard-trade")

urlpatterns = [
    path("billing/catalogue/", views.CatalogueView.as_view(), name="billing-catalogue"),
    path("billing/variations/<str:kind>/", views.VariationsView.as_view(), name="billing-variations"),
    path("billing/verify-customer/", views.VerifyCustomerView.as_view(), name="billing-verify"),
    path("billing/giftcards/quote/", views.GiftCardQuoteView.as_view(), name="giftcard-quote"),
    re_path(r"^billing/giftcards/(?P<what>countries|products|sell-catalog|sell-rates)/$",
            views.GiftCardCatalogView.as_view(), name="giftcard-catalog"),
    path("billing/rmb/", views.RmbView.as_view(), name="billing-rmb"),
    path("billing/wallet/", views.WalletView.as_view(), name="billing-wallet"),
    path("billing/wallet/fund/", views.WalletFundView.as_view(), name="billing-wallet-fund"),
    path("billing/webhook/vtu/<str:token>/", views.VTUWebhookView.as_view(), name="billing-vtu-webhook"),
    *router.urls,
]
