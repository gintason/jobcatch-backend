"""
Job listing packages.

  GET  /listings/packages/            -> active packages (public: pricing page)
  GET  /listings/credits/             -> employer's credits + purchase history
  POST /listings/purchase/            -> {package, job?, gateway?} -> checkout URL
                                         (free packages activate immediately)
"""
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsEmployer
from apps.payments.services import CheckoutError

from .models import JobPackage, PackagePurchase
from .serializers import JobPackageSerializer, PackagePurchaseSerializer, PurchaseRequestSerializer
from .services import credit_summary, purchase_package


class PackageListView(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = JobPackageSerializer
    pagination_class = None
    queryset = JobPackage.objects.filter(is_active=True)


class MyCreditsView(APIView):
    permission_classes = [IsEmployer]

    def get(self, request):
        employer = request.user.employer_profile
        summary = credit_summary(employer)
        history = PackagePurchase.objects.filter(employer=employer).exclude(
            status="pending").select_related("package", "payment")[:50]
        return Response({
            "total_credits": summary["total_credits"],
            "featured_credits": summary["featured_credits"],
            "active": PackagePurchaseSerializer(summary["purchases"], many=True).data,
            "history": PackagePurchaseSerializer(history, many=True).data,
        })


class PurchasePackageView(APIView):
    permission_classes = [IsEmployer]

    def post(self, request):
        ser = PurchaseRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            result = purchase_package(
                user=request.user, package=ser.validated_data["package"],
                gateway=ser.validated_data.get("gateway"), job_id=ser.validated_data.get("job"),
            )
        except CheckoutError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        payment = result["payment"]
        return Response({
            "purchase": PackagePurchaseSerializer(result["purchase"]).data,
            "authorization_url": result["authorization_url"],
            "reference": payment.reference if payment else None,
            "job_published": bool(result["job"] and result["job"].listing_status == "active"),
        }, status=status.HTTP_201_CREATED)
