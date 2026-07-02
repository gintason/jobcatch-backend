from rest_framework import serializers

from .models import CV


class CVSerializer(serializers.ModelSerializer):
    class Meta:
        model = CV
        fields = ("id", "title", "file", "created_at")
        read_only_fields = ("id", "created_at")
