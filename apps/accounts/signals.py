"""Create every role profile when a user is created.

JobCatch is a single-account platform: a user signs up once (no role chosen),
then picks how they want to use the platform from the role hub, switching
freely between Customer, Artisan, Employer and Job Seeker.

For that to work, every user owns ALL FOUR profiles from the moment they
register, so whichever mode they switch into, `request.user.<x>_profile`
already exists and the viewsets never 404.

ADMIN accounts get profiles too (harmless) so staff can inspect any flow.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    ArtisanProfile,
    CustomerProfile,
    EmployerProfile,
    JobSeekerProfile,
    User,
)

ALL_PROFILE_MODELS = (
    CustomerProfile,
    ArtisanProfile,
    EmployerProfile,
    JobSeekerProfile,
)


def _create_profile(profile_model, user):
    """Create one profile, filling required non-blank fields with sane defaults."""
    defaults = {}
    if profile_model is EmployerProfile:
        # company_name is required; the employer edits it on their dashboard.
        defaults["company_name"] = user.full_name or "Unnamed Company"
    profile_model.objects.get_or_create(user=user, defaults=defaults)


@receiver(post_save, sender=User)
def create_role_profiles(sender, instance, created, **kwargs):
    if not created:
        return
    for profile_model in ALL_PROFILE_MODELS:
        _create_profile(profile_model, instance)
