from django.urls import path

from . import views

urlpatterns = [
    path("listings/packages/", views.PackageListView.as_view(), name="listing-packages"),
    path("listings/credits/", views.MyCreditsView.as_view(), name="listing-credits"),
    path("listings/purchase/", views.PurchasePackageView.as_view(), name="listing-purchase"),
]
