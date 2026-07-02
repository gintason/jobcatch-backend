from rest_framework.routers import DefaultRouter

from .views import SubscriptionViewSet

router = DefaultRouter()
router.register("subscriptions", SubscriptionViewSet, basename="subscription")

urlpatterns = router.urls
