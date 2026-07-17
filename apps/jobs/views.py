"""
Jobs & Applications API.

  CVs           -> job seekers manage their own (from Phase 2A)
  Jobs          -> employers create/manage their own; anyone browses open jobs
  Applications  -> seekers apply & track; employers review & move the pipeline
"""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import UserRole
from apps.common.permissions import IsEmployer, IsJobSeeker

from .models import Application, ApplicationStatus, CV, Job
from .serializers import (
    ApplicationCreateSerializer,
    ApplicationSerializer,
    CVSerializer,
    JobSerializer,
    JobWriteSerializer,
)
from .services import transition_application


class CVViewSet(viewsets.ModelViewSet):
    """Job seekers upload/list/delete their own CVs."""

    serializer_class = CVSerializer
    permission_classes = [IsJobSeeker]

    def get_queryset(self):
        return CV.objects.filter(seeker=self.request.user.job_seeker_profile)

    def perform_create(self, serializer):
        serializer.save(seeker=self.request.user.job_seeker_profile)


class JobViewSet(viewsets.ModelViewSet):
    """Employers manage their own postings; everyone can browse open jobs."""

    def get_serializer_class(self):
        return JobWriteSerializer if self.action in ("create", "update", "partial_update") else JobSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsEmployer()]

    def get_queryset(self):
        qs = Job.objects.select_related("employer__user", "category")
        # Write actions: restrict to the employer's own jobs.
        if self.action not in ("list", "retrieve"):
            return qs.filter(employer=self.request.user.employer_profile)
        # ?mine=true -> the employer's own jobs (incl. closed).

        user = self.request.user
        employer_profile = getattr(user, "employer_profile", None)
        if self.request.query_params.get("mine") == "true" and employer_profile:
            return qs.filter(employer=employer_profile)
        
        # Public browse: open jobs, with optional ?category= and ?q= (title search).
        category = self.request.query_params.get("category")
        q = self.request.query_params.get("q")
        if category:
            qs = qs.filter(category_id=category)
        if q:
            qs = qs.filter(title__icontains=q)
        return qs.filter(is_open=True)

    @action(detail=True, methods=["get"], permission_classes=[IsEmployer])
    def applications(self, request, pk=None):
        """Employer views applicants to their OWN job."""
        job = self.get_object()  # get_object uses the write-scoped queryset -> own jobs only
        qs = Application.objects.filter(job=job).select_related("seeker__user")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(ApplicationSerializer(page, many=True).data)


class ApplicationViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        return ApplicationCreateSerializer if self.action == "create" else ApplicationSerializer

    def get_permissions(self):
        if self.action == "create":
            return [IsJobSeeker()]
        if self.action in ("shortlist", "reject", "hire"):
            return [IsEmployer()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        qs = Application.objects.select_related("job__employer__user", "seeker__user")
        if user.role == UserRole.ADMIN:
            return qs
        # Paired roles: a supply-side user (artisan/job_seeker) tracks their own
        # applications; a demand-side user (customer/employer) sees applicants to
        # their own jobs. Every user now has both profiles, so scope by profile.
        from django.db.models import Q
        conditions = Q()
        if getattr(user, "job_seeker_profile", None):
            conditions |= Q(seeker__user=user)
        if getattr(user, "employer_profile", None):
            conditions |= Q(job__employer__user=user)
        if not conditions:
            return qs.none()
        return qs.filter(conditions)
    

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        job = request.query_params.get("job")
        if job:
            qs = qs.filter(job_id=job)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        app = ser.save()
        return Response(ApplicationSerializer(app).data, status=status.HTTP_201_CREATED)

    # ---- recruitment pipeline (employer, own job's applications only) ----
    def _move(self, request, to_status):
        app = self.get_object()  # 404 unless it's an application to the employer's own job
        transition_application(app, to_status)
        return Response(ApplicationSerializer(app).data)

    @action(detail=True, methods=["post"])
    def shortlist(self, request, pk=None):
        return self._move(request, ApplicationStatus.SHORTLISTED)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._move(request, ApplicationStatus.REJECTED)

    @action(detail=True, methods=["post"])
    def hire(self, request, pk=None):
        return self._move(request, ApplicationStatus.HIRED)
