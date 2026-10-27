"""
Scoped throttles for brute-force-sensitive endpoints.

DRF's ScopedRateThrottle ignores a `scope` class attribute: it overwrites it
with the *view's* `throttle_scope`, and when the view has none it allows every
request. These classes pin the scope on the throttle itself, so adding
`throttle_classes = [OTPThrottle]` to a view is enough to enforce the rate.
"""
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class _FixedScopeThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return SimpleRateThrottle.allow_request(self, request, view)


class LoginThrottle(_FixedScopeThrottle):
    scope = "login"


class OTPThrottle(_FixedScopeThrottle):
    scope = "otp"
