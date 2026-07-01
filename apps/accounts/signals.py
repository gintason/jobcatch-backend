"""Create the matching one-to-one profile whenever a user is created."""
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

_PROFILE_FOR_ROLE = {
    UserRole.CUSTOMER: CustomerProfile,
    UserRole.ARTISAN: ArtisanProfile,
    UserRole.EMPLOYER: EmployerProfile,
    UserRole.JOB_SEEKER: JobSeekerProfile,
    # ADMIN has no domain profile.
}


@receiver(post_save, sender=User)
def create_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    profile_model = _PROFILE_FOR_ROLE.get(instance.role)
    if profile_model is None:
        return
    # EmployerProfile needs a company_name; use a placeholder the user edits later.
    defaults = {}
    if profile_model is EmployerProfile:
        defaults["company_name"] = instance.full_name or "Unnamed Company"
    profile_model.objects.get_or_create(user=instance, defaults=defaults)
