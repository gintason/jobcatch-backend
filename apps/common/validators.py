"""File-upload validation — enforces the JPG/PNG/PDF/MP4 policy (spec §22)."""
import os

from django.conf import settings
from django.core.exceptions import ValidationError


def validate_upload_extension(file):
    ext = os.path.splitext(file.name)[1].lower().lstrip(".")
    allowed = settings.ALLOWED_UPLOAD_EXTENSIONS
    if ext not in allowed:
        raise ValidationError(f"Unsupported file type '.{ext}'. Allowed: {', '.join(allowed)}.")


def validate_upload_size(file):
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file.size > max_bytes:
        raise ValidationError(f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit.")


def validate_upload(file):
    validate_upload_extension(file)
    validate_upload_size(file)
