from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/complaints", views.AdminComplaintViewSet, basename="admin-complaint")

urlpatterns = [
    path("complaints/", views.SubmitComplaintView.as_view(), name="complaint-submit"),
    path("complaints/mine/", views.MyComplaintsView.as_view(), name="complaint-mine"),
    path("complaints/track/", views.TrackComplaintView.as_view(), name="complaint-track"),
    *router.urls,
]
