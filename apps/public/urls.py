from django.urls import path

from .views import (
    ArtisanBrowseList,
    ArtisanDetail,
    FeaturedArtisanList,
    PublicCategoryList,
)

urlpatterns = [
    path("public/featured-artisans/", FeaturedArtisanList.as_view(), name="public-featured-artisans"),
    path("public/categories/", PublicCategoryList.as_view(), name="public-categories"),
    path("public/artisans/", ArtisanBrowseList.as_view(), name="public-artisan-browse"),
    path("public/artisans/<uuid:id>/", ArtisanDetail.as_view(), name="public-artisan-detail"),
]
