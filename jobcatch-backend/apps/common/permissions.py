"""
Role-Based Access Control.

Three enforcement layers (see architecture §3.4):
  1. Authentication  -> DEFAULT_PERMISSION_CLASSES = IsAuthenticated (+ token_version check)
  2. Role            -> the RolePermission subclasses below
  3. Object ownership-> IsOwner / has_object_permission on each viewset

IMPORTANT: role is read from request.user (the DB object loaded during
authentication), NOT from the raw JWT claim — so a stale/tampered token
cannot escalate privileges.

--- Multi-role capability pairing (added) --------------------------------
A user's `role` is their PRIMARY/active identity, but JobCatch pairs roles
that naturally belong together so one account can act on both sides of a
transaction without a second signup:

    demand side  : customer  <-> employer   (book artisans, post jobs, hire)
    supply side  : artisan   <-> job_seeker (offer services, apply for jobs)

The permission classes below therefore accept EITHER role in a pair. The
paired one-to-one profile is guaranteed to exist (created by the accounts
signal for new users, and backfilled by a data migration for existing ones),
so a customer always has an employer_profile, an artisan always has a
job_seeker_profile, etc. — the viewsets can rely on request.user.<profile>.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.models import UserRole

# The two capability pairs. Membership in a pair grants that pair's powers.
DEMAND_ROLES = {UserRole.CUSTOMER, UserRole.EMPLOYER}
SUPPLY_ROLES = {UserRole.ARTISAN, UserRole.JOB_SEEKER}


class RolePermission(BasePermission):
    """
    Base class. Set `required_role` on a subclass for an exact-role check, or
    `allowed_roles` (a set) to allow any role in a capability pair.
    """

    required_role: str | None = None
    allowed_roles: set | None = None

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if self.allowed_roles is not None:
            return user.role in self.allowed_roles
        return self.required_role is None or user.role == self.required_role


# --- Demand side: customers and employers share these capabilities ---
class IsCustomer(RolePermission):
    # Booking an artisan is a demand-side action; employers can do it too.
    allowed_roles = DEMAND_ROLES


class IsEmployer(RolePermission):
    # Posting jobs / hiring is a demand-side action; customers can do it too.
    allowed_roles = DEMAND_ROLES


# --- Supply side: artisans and job seekers share these capabilities ---
class IsArtisan(RolePermission):
    # Offering services is a supply-side action; job seekers can do it too.
    allowed_roles = SUPPLY_ROLES


class IsJobSeeker(RolePermission):
    # Applying for jobs is a supply-side action; artisans can do it too.
    allowed_roles = SUPPLY_ROLES


class IsAdmin(BasePermission):
    """Platform admin — requires role AND staff flag (defence in depth)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == UserRole.ADMIN
            and user.is_staff
        )


class IsVerified(BasePermission):
    """Gate actions that require a completed identity verification."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_identity_verified)


class IsOwnerOrReadOnly(BasePermission):
    """
    Object-level ownership. Reads allowed to any authenticated user;
    writes only to the owner. Subclasses/viewsets define `owner_field`
    (default 'user') resolving to the owning User.
    """

    owner_field = "user"

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        owner = getattr(obj, getattr(view, "owner_field", self.owner_field), None)
        return owner == request.user
