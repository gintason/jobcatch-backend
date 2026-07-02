from rest_framework.routers import DefaultRouter

from .views import CVViewSet

router = DefaultRouter()
router.register("cvs", CVViewSet, basename="cv")

urlpatterns = router.urls
