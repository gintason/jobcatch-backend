"""Category (admin-managed) and Service (artisan-managed) APIs."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.common.permissions import IsAdmin, IsArtisan

from .models import Category, Service
from .serializers import CategorySerializer, ServiceSerializer


class CategoryViewSet(viewsets.ModelViewSet):
    """Anyone authenticated can read; only admins can create/edit/delete."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdmin()]


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Read: any authenticated user (customers browse the marketplace).
    Write: artisans, restricted to their OWN services.
    """

    serializer_class = ServiceSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsArtisan()]

    def get_queryset(self):
        qs = Service.objects.select_related("artisan__user", "category")
        # On write actions, restrict to the caller's own services.
        if self.action not in ("list", "retrieve"):
            return qs.filter(artisan=self.request.user.artisan_profile)
        # ?mine=true -> the calling artisan's own services (incl. inactive).
        user = self.request.user
        if (self.request.query_params.get("mine") == "true"
                and getattr(user, "role", None) == "artisan"):
            return qs.filter(artisan=user.artisan_profile)
        # Public listing: optional ?category= and ?artisan= filters.
        category = self.request.query_params.get("category")
        artisan = self.request.query_params.get("artisan")
        if category:
            qs = qs.filter(category_id=category)
        if artisan:
            qs = qs.filter(artisan_id=artisan)
        return qs.filter(is_active=True)

    def perform_create(self, serializer):
        serializer.save(artisan=self.request.user.artisan_profile)
