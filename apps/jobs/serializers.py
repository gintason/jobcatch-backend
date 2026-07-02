"""Serializers for CVs, Jobs, and Applications."""
from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import Application, CV, Job


# ---------------------------------------------------------------- CVs (from 2A)
class CVSerializer(serializers.ModelSerializer):
    class Meta:
        model = CV
        fields = ("id", "title", "file", "created_at")
        read_only_fields = ("id", "created_at")


# ---------------------------------------------------------------- Jobs
class JobWriteSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(write_only=True, required=False)
    longitude = serializers.FloatField(write_only=True, required=False)

    class Meta:
        model = Job
        fields = ("id", "title", "description", "category",
                  "salary_min", "salary_max", "is_open",
                  "latitude", "longitude")
        read_only_fields = ("id",)

    def _apply(self, validated):
        lat = validated.pop("latitude", None)
        lng = validated.pop("longitude", None)
        if lat is not None and lng is not None:
            validated["location"] = Point(lng, lat)
        return validated

    def create(self, validated):
        validated = self._apply(validated)
        validated["employer"] = self.context["request"].user.employer_profile
        return super().create(validated)

    def update(self, instance, validated):
        return super().update(instance, self._apply(validated))


class JobSerializer(serializers.ModelSerializer):
    employer_company = serializers.CharField(source="employer.company_name", read_only=True)
    location = serializers.SerializerMethodField()
    application_count = serializers.IntegerField(source="applications.count", read_only=True)

    class Meta:
        model = Job
        fields = ("id", "title", "description", "category",
                  "employer", "employer_company", "location",
                  "salary_min", "salary_max", "is_open",
                  "application_count", "created_at")
        read_only_fields = fields

    def get_location(self, obj):
        return {"latitude": obj.location.y, "longitude": obj.location.x} if obj.location else None


# ---------------------------------------------------------------- Applications
class ApplicationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = ("id", "job", "cv", "cover_letter")
        read_only_fields = ("id",)

    def validate(self, data):
        request = self.context["request"]
        seeker = request.user.job_seeker_profile
        job = data["job"]
        if not job.is_open:
            raise serializers.ValidationError("This job is no longer accepting applications.")
        if data["cv"].seeker_id != seeker.id:
            raise serializers.ValidationError("You can only apply with your own CV.")
        if Application.objects.filter(job=job, seeker=seeker).exists():
            raise serializers.ValidationError("You have already applied to this job.")
        return data

    def create(self, validated):
        validated["seeker"] = self.context["request"].user.job_seeker_profile
        return super().create(validated)


class ApplicationSerializer(serializers.ModelSerializer):
    job_title = serializers.CharField(source="job.title", read_only=True)
    seeker_email = serializers.EmailField(source="seeker.user.email", read_only=True)

    class Meta:
        model = Application
        fields = ("id", "job", "job_title", "seeker", "seeker_email",
                  "cv", "cover_letter", "status", "created_at")
        read_only_fields = fields
