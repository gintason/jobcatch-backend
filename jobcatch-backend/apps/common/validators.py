"""File-upload validation — enforces the JPG/PNG/PDF/MP4 policy (spec §22)."""
import os

from django.conf import settings
from django.core.exceptions import ValidationError

IMAGE_EXTS = ("jpg", "jpeg", "png")
DOCUMENT_EXTS = ("pdf", "jpg", "jpeg", "png")
VIDEO_EXTS = ("mp4",)


def _ext(file):
    return os.path.splitext(file.name)[1].lower().lstrip(".")


def _check_size(file, max_mb):
    if file.size > max_mb * 1024 * 1024:
        raise ValidationError(f"File exceeds {max_mb} MB limit.")


def validate_upload(file):
    """Any allowed type (JPG/PNG/PDF/MP4), default size limit."""
    ext = _ext(file)
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '.{ext}'. "
            f"Allowed: {', '.join(settings.ALLOWED_UPLOAD_EXTENSIONS)}."
        )
    _check_size(file, settings.MAX_UPLOAD_SIZE_MB)


def validate_image_upload(file):
    if _ext(file) not in IMAGE_EXTS:
        raise ValidationError(f"Images must be one of: {', '.join(IMAGE_EXTS)}.")
    _check_size(file, settings.MAX_UPLOAD_SIZE_MB)


def validate_document_upload(file):
    """CVs, certificates, IDs — PDF or image, no video."""
    if _ext(file) not in DOCUMENT_EXTS:
        raise ValidationError(f"Documents must be one of: {', '.join(DOCUMENT_EXTS)}.")
    _check_size(file, settings.MAX_UPLOAD_SIZE_MB)


def validate_video_upload(file):
    """Artisan job videos — MP4 only, larger size ceiling."""
    if _ext(file) not in VIDEO_EXTS:
        raise ValidationError("Videos must be MP4.")
    _check_size(file, settings.MAX_VIDEO_SIZE_MB)
