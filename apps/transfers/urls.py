from django.urls import path

from . import views

urlpatterns = [
    path("transfers/", views.HistoryView.as_view(), name="transfers"),
    path("transfers/overview/", views.OverviewView.as_view(), name="transfers-overview"),
    path("transfers/pin/", views.PinView.as_view(), name="transfers-pin"),
    path("transfers/lookup/", views.LookupView.as_view(), name="transfers-lookup"),
    path("transfers/wallet/", views.WalletTransferView.as_view(), name="transfers-wallet"),
    path("transfers/banks/", views.BanksView.as_view(), name="transfers-banks"),
    path("transfers/resolve/", views.ResolveView.as_view(), name="transfers-resolve"),
    path("transfers/bank/", views.BankTransferView.as_view(), name="transfers-bank"),
    path("transfers/bank/<uuid:pk>/", views.BankTransferDetailView.as_view(), name="transfers-bank-detail"),
]
