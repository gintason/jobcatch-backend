"""Serializers for CVs, Jobs, and Applications."""
from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import Application, CV, Job


def normalise_multiline(text):
    """
    Keep the employer's formatting exactly — paragraphs, blank lines, bullet
    indentation — while normalising Windows/old-Mac line endings so every
    client renders the same thing. Only trailing whitespace is dropped.
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()


# ---------------------------------------------------------------- CVs (from 2A)
class CVSerializer(serializers.ModelSerializer):
    class Meta:
        model = CV
        fields = ("id", "title", "file", "created_at")
        read_only_fields = ("id", "created_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.file:
            data["file"] = instance.file.url  # relative /media/... for the proxy
        return data


# ---------------------------------------------------------------- Jobs
class JobWriteSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(write_only=True, required=False)
    longitude = serializers.FloatField(write_only=True, required=False)
    # DRF's default trim_whitespace would strip leading indentation from the
    # first line; formatting is normalised by normalise_multiline instead.
    description = serializers.CharField(trim_whitespace=False)
    # Ask to publish straight away (spends a listing credit if one is available).
    publish = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Job
        fields = ("id", "title", "description", "category",
                  "salary_min", "salary_max", "is_open",
                  "latitude", "longitude", "publish")
        read_only_fields = ("id",)

    def validate_description(self, value):
        value = normalise_multiline(value)
        if not value.strip():
            raise serializers.ValidationError("Describe the role.")
        return value

    def validate(self, data):
        lo, hi = data.get("salary_min"), data.get("salary_max")
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({"salary_max": "Must be at least the minimum salary."})
        return data

    def _apply(self, validated):
        validated.pop("publish", None)
        lat = validated.pop("latitude", None)
        lng = validated.pop("longitude", None)
        if lat is not None and lng is not None:
            validated["location"] = Point(lng, lat)
        return validated

    def create(self, validated):
        validated = self._apply(validated)
        validated["employer"] = self.context["request"].user.employer_profile
        # Every employer-created job starts as a draft; JobViewSet.create then
        # publishes it (free when paid listings are off, else by spending a credit).
        validated["published_at"] = None
        return super().create(validated)

    def update(self, instance, validated):
        return super().update(instance, self._apply(validated))


class JobSerializer(serializers.ModelSerializer):
    def validate_category(self, value):
        from apps.catalog.models import CategoryKind

        if value.kind != CategoryKind.JOB:
            raise serializers.ValidationError(
                "Jobs must use a job-listing category."
            )
        return value

    employer_company = serializers.CharField(source="employer.company_name", read_only=True)
    employer_verified = serializers.BooleanField(source="employer.is_cac_verified", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    location = serializers.SerializerMethodField()
    application_count = serializers.IntegerField(source="applications.count", read_only=True)
    listing_status = serializers.CharField(read_only=True)

    class Meta:
        model = Job
        fields = ("id", "title", "description", "category", "category_name",
                  "employer", "employer_company", "employer_verified", "location",
                  "salary_min", "salary_max", "is_open",
                  "application_count", "listing_status", "is_featured",
                  "published_at", "expires_at", "created_at")
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
        if job.listing_status != "active":
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
    seeker_name = serializers.CharField(source="seeker.user.full_name", read_only=True)
    cv_file = serializers.SerializerMethodField()
    # Job seeker Pro members get a badge on their applications.
    seeker_is_pro = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = ("id", "job", "job_title", "seeker", "seeker_email", "seeker_name",
                  "seeker_is_pro", "cv", "cv_file", "cover_letter", "status", "created_at")
        read_only_fields = fields

    def get_seeker_is_pro(self, obj):
        from apps.engagement.services import active_seeker_pro

        return active_seeker_pro(obj.seeker.user) is not None

    def get_cv_file(self, obj):
        return obj.cv.file.url if obj.cv and obj.cv.file else None
