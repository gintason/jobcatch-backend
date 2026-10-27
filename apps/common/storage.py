"""
Private file storage for KYC and verification documents.

Business certificates, utility bills and government IDs must never be reachable
by URL alone. Locally / on a Render disk they go to PRIVATE_MEDIA_ROOT, which no
route serves; with S3 configured they go to a private prefix and are only ever
handed out as short-lived signed URLs. Either way the API streams them through a
permission-checked download view (see apps.kyc.views).

`private_storage` is passed to FileField(storage=...) as a callable, so the
storage is resolved at runtime and migrations stay environment-independent.
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """
    Filesystem storage rooted at PRIVATE_MEDIA_ROOT, read on every access
    (the stock class captures the location once at import time, which would
    ignore per-environment/test overrides). No base_url: never URL-addressable.
    """

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def deconstruct(self):
        return ("apps.common.storage.PrivateFileSystemStorage", (), {})


def private_storage():
    if getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
        from storages.backends.s3boto3 import S3Boto3Storage

        return S3Boto3Storage(
            location="private",
            default_acl="private",
            querystring_auth=True,
            querystring_expire=300,
            file_overwrite=False,
        )
    return PrivateFileSystemStorage()


def _private_path(prefix, instance, filename):
    """
    <prefix>/<owner-id>/<random>.<ext> — no user-supplied filename on disk,
    so paths can't be guessed or used for traversal.
    """
    ext = os.path.splitext(filename)[1].lower()[:10]
    owner = getattr(instance, "owner_id_for_storage", None) or "misc"
    return f"{prefix}/{owner}/{uuid.uuid4().hex}{ext}"


# Module-level functions (not closures) so migrations can serialise them.
def kyc_document_upload_to(instance, filename):
    return _private_path("kyc", instance, filename)


def artisan_document_upload_to(instance, filename):
    return _private_path("artisan-verification", instance, filename)
