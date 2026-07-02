from rest_framework.routers import DefaultRouter

from .views import ApplicationViewSet, CVViewSet, JobViewSet

router = DefaultRouter()
router.register("cvs", CVViewSet, basename="cv")
router.register("jobs", JobViewSet, basename="job")
router.register("applications", ApplicationViewSet, basename="application")

urlpatterns = router.urls
