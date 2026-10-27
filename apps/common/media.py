"""
Public media serving with a hard block on the private document area.

On Render the persistent disk is mounted at MEDIA_ROOT and private KYC
documents live in MEDIA_ROOT/_private. Django's `serve` normalises the path
("a/../_private/x" -> "_private/x"), so the check is done on the normalised
path, not with a URL regex.
"""
import posixpath

from django.conf import settings
from django.http import Http404
from django.views.static import serve

PRIVATE_PREFIX = "_private"


def serve_public_media(request, path):
    normalised = posixpath.normpath(path).lstrip("/")
    if normalised == PRIVATE_PREFIX or normalised.startswith(PRIVATE_PREFIX + "/"):
        raise Http404("Not found.")
    return serve(request, normalised, document_root=settings.MEDIA_ROOT)
