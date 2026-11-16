"""Reviews: customers rate completed bookings; anyone can read an artisan's reviews."""
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.permissions import IsCustomer

from .models import Review
from .serializers import ReviewCreateSerializer, ReviewSerializer


class ReviewViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        return ReviewCreateSerializer if self.action == "create" else ReviewSerializer

    def get_permissions(self):
        return [IsCustomer()] if self.action == "create" else [IsAuthenticated()]

    def get_queryset(self):
        qs = Review.objects.filter(is_visible=True).select_related("author", "target")
        artisan = self.request.query_params.get("artisan")  # target user id
        if artisan:
            qs = qs.filter(target_id=artisan)
        return qs

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        review = ser.save()  # post_save signal recomputes the artisan's rating
        return Response(
            ReviewSerializer(review).data, status=status.HTTP_201_CREATED
        )
