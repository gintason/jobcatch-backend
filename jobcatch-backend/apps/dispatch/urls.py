from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("dispatch/deliveries", views.DeliveryViewSet, basename="delivery")
router.register("dispatch/rider/jobs", views.RiderJobViewSet, basename="rider-job")
router.register("admin/riders", views.AdminRiderViewSet, basename="admin-rider")

urlpatterns = [
    path("dispatch/rates/", views.RatesView.as_view(), name="dispatch-rates"),
    path("dispatch/quote/", views.QuoteView.as_view(), name="dispatch-quote"),
    path("dispatch/banks/", views.BanksView.as_view(), name="dispatch-banks"),
    path("dispatch/rider/", views.RiderProfileView.as_view(), name="dispatch-rider"),
    path("dispatch/rider/payout-account/", views.PayoutAccountView.as_view(), name="dispatch-payout"),
    path("dispatch/rider/availability/", views.AvailabilityView.as_view(), name="dispatch-availability"),
    path("dispatch/rider/requests/", views.OpenRequestsView.as_view(), name="dispatch-requests"),
    *router.urls,
]
