"""
Role-Based Access Control.

Three enforcement layers (see architecture §3.4):
  1. Authentication  -> DEFAULT_PERMISSION_CLASSES = IsAuthenticated (+ token_version check)
  2. Role            -> the RolePermission subclasses below
  3. Object ownership-> IsOwner / has_object_permission on each viewset

IMPORTANT: role is read from request.user (the DB object loaded during
authentication), NOT from the raw JWT claim — so a stale/tampered token
cannot escalate privileges.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.models import UserRole


class RolePermission(BasePermission):
    """Base class: set `required_role` on a subclass."""

    required_role: str | None = None

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (self.required_role is None or user.role == self.required_role)
        )


class IsCustomer(RolePermission):
    required_role = UserRole.CUSTOMER


class IsArtisan(RolePermission):
    required_role = UserRole.ARTISAN


class IsEmployer(RolePermission):
    required_role = UserRole.EMPLOYER


class IsJobSeeker(RolePermission):
    required_role = UserRole.JOB_SEEKER


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
