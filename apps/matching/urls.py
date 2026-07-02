from django.urls import path

from .views import ArtisanMatchView

urlpatterns = [
    path("matching/artisans/", ArtisanMatchView.as_view(), name="match-artisans"),
]
