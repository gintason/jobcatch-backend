from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet, ServiceViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("services", ServiceViewSet, basename="service")

urlpatterns = router.urls
