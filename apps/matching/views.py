"""Geo-matching endpoint: GET /api/v1/matching/artisans/?latitude=&longitude=&..."""
from django.conf import settings
from django.contrib.gis.geos import Point
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import match_artisans


def _serialize(artisan, distance_km, score):
    services = [
        {"id": str(s.id), "title": s.title, "category": str(s.category_id),
         "base_price": str(s.base_price)}
        for s in artisan.services.filter(is_active=True)
    ]
    return {
        "artisan_id": str(artisan.id),
        "user_id": str(artisan.user_id),
        "full_name": artisan.user.full_name,
        "avg_rating": str(artisan.avg_rating),
        "rating_count": artisan.rating_count,
        "is_featured": artisan.is_featured,
        "is_work_verified": artisan.is_work_verified,
        "distance_km": round(distance_km, 2),
        "score": score,
        "services": services,
    }


class ArtisanMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lat = request.query_params.get("latitude")
        lng = request.query_params.get("longitude")
        if lat is None or lng is None:
            return Response({"detail": "latitude and longitude are required."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            point = Point(float(lng), float(lat))  # (x=lng, y=lat)
            radius_km = float(request.query_params.get(
                "radius_km", settings.MATCHING_DEFAULT_RADIUS_KM))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid coordinates or radius."},
                            status=status.HTTP_400_BAD_REQUEST)

        results = match_artisans(
            point=point, radius_km=radius_km,
            category=request.query_params.get("category"),
            q=request.query_params.get("q"),
        )
        return Response([_serialize(a, d, s) for a, d, s in results])
