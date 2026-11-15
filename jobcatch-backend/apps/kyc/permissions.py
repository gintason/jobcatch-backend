from rest_framework.exceptions import PermissionDenied

from apps.common.permissions import IsEmployer

from .services import can_post_jobs


class IsVerifiedEmployer(IsEmployer):
    """
    Demand-side role AND a verified business (when KYC_REQUIRED_TO_POST_JOBS).
    Raises a coded 403 so the web app can route the employer to the KYC flow.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not can_post_jobs(request.user):
            raise PermissionDenied({
                "detail": "Verify your business to post jobs. It takes a few minutes: "
                          "confirm your email and phone, add your CAC, TIN and director "
                          "details, and upload your documents.",
                "code": "kyc_required",
            })
        return True
