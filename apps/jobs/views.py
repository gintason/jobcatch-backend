"""Job-seeker CV management (Phase 2A). Jobs & Applications APIs land in Phase 2C."""
from rest_framework import viewsets

from apps.common.permissions import IsJobSeeker

from .models import CV
from .serializers import CVSerializer


class CVViewSet(viewsets.ModelViewSet):
    """Job seekers upload/list/delete their own CVs."""

    serializer_class = CVSerializer
    permission_classes = [IsJobSeeker]

    def get_queryset(self):
        return CV.objects.filter(seeker=self.request.user.job_seeker_profile)

    def perform_create(self, serializer):
        serializer.save(seeker=self.request.user.job_seeker_profile)
