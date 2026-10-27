from django.urls import path

from . import views

urlpatterns = [
    path("engagement/prompts/", views.PromptListView.as_view(), name="prompts"),
    path("engagement/prompts/<slug:key>/<str:event>/", views.PromptEventView.as_view(),
         name="prompt-event"),
    path("engagement/seeker-pro/", views.SeekerProView.as_view(), name="seeker-pro"),
    path("engagement/seeker-pro/checkout/", views.SeekerProCheckoutView.as_view(),
         name="seeker-pro-checkout"),
    path("engagement/job-alerts/", views.JobAlertView.as_view(), name="job-alerts"),
]
