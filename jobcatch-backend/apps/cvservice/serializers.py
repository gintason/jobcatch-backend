from rest_framework import serializers

from .models import CVReferral, CVServiceAccess, CVSubmission


class CVServiceAccessSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()

    class Meta:
        model = CVServiceAccess
        fields = ("is_active", "paid_at", "price")
        read_only_fields = fields

    def get_price(self, obj):
        from django.conf import settings
        return getattr(settings, "CV_SERVICE_PRICE", 5000)


class CVSubmissionSerializer(serializers.ModelSerializer):
    cv_file_url = serializers.SerializerMethodField()

    class Meta:
        model = CVSubmission
        fields = ("id", "cv_file", "cv_file_url", "headline", "note", "status", "created_at")
        read_only_fields = ("id", "status", "created_at", "cv_file_url")
        extra_kwargs = {"cv_file": {"write_only": True}}

    def get_cv_file_url(self, obj):
        return obj.cv_file.url if obj.cv_file else None


class ReferredCVSerializer(serializers.ModelSerializer):
    """What an EMPLOYER sees: a CV the JobCatch admin forwarded to them."""

    seeker_name = serializers.CharField(source="submission.seeker.full_name", read_only=True)
    seeker_email = serializers.CharField(source="submission.seeker.email", read_only=True)
    headline = serializers.CharField(source="submission.headline", read_only=True)
    cv_file_url = serializers.SerializerMethodField()

    class Meta:
        model = CVReferral
        fields = ("id", "seeker_name", "seeker_email", "headline",
                  "admin_note", "cv_file_url", "created_at")
        read_only_fields = fields

    def get_cv_file_url(self, obj):
        f = obj.submission.cv_file
        return f.url if f else None
