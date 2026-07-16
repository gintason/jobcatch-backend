"""Create the user's role profile(s) whenever a user is created.

Multi-role pairing: JobCatch lets one account act on both sides of a paired
role, so on registration we create BOTH profiles in the user's capability pair
(not just the primary one). This guarantees request.user.<profile> always
exists for any capability the user is allowed to exercise:

    customer / employer  -> both CustomerProfile AND EmployerProfile
    artisan  / job_seeker-> both ArtisanProfile  AND JobSeekerProfile

ADMIN has no domain profile.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    ArtisanProfile,
    CustomerProfile,
    EmployerProfile,
    JobSeekerProfile,
    User,
    UserRole,
)

# Each role maps to the FULL set of profiles that role's capabilities need.
_PROFILES_FOR_ROLE = {
    UserRole.CUSTOMER: (CustomerProfile, EmployerProfile),
    UserRole.EMPLOYER: (EmployerProfile, CustomerProfile),
    UserRole.ARTISAN: (ArtisanProfile, JobSeekerProfile),
    UserRole.JOB_SEEKER: (JobSeekerProfile, ArtisanProfile),
    # ADMIN has no domain profile.
}


def _create_profile(profile_model, user):
    """Create one profile, filling any required non-blank fields with sane defaults."""
    defaults = {}
    if profile_model is EmployerProfile:
        # company_name is required; the user edits it later.
        defaults["company_name"] = user.full_name or "Unnamed Company"
    profile_model.objects.get_or_create(user=user, defaults=defaults)


@receiver(post_save, sender=User)
def create_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    for profile_model in _PROFILES_FOR_ROLE.get(instance.role, ()):  # noqa: B007
        _create_profile(profile_model, instance)
