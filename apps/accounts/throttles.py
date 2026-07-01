"""Scoped throttles for brute-force-sensitive endpoints."""
from rest_framework.throttling import ScopedRateThrottle


class LoginThrottle(ScopedRateThrottle):
    scope = "login"


class OTPThrottle(ScopedRateThrottle):
    scope = "otp"
