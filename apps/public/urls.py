from django.urls import path

from .views import FeaturedArtisanList, PublicCategoryList

urlpatterns = [
    path("public/featured-artisans/", FeaturedArtisanList.as_view(), name="public-featured-artisans"),
    path("public/categories/", PublicCategoryList.as_view(), name="public-categories"),
]
