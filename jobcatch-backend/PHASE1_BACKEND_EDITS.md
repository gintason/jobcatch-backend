# Phase 1 backend edits (2 files to edit by hand)

The zip already contains the two files you can drop in as-is:
- `apps/accounts/signals.py`  (creates all four profiles on registration)
- `apps/accounts/migrations/0007_backfill_all_profiles.py`  (backfills existing users)

The two edits below must be made in files I don't have a copy of.

---

## 1. Add the role-switch endpoint

**`apps/accounts/api_views.py`** — add this view (put it near the other
authenticated views, and make sure `APIView`, `Response`, `IsAuthenticated`
and `UserRole` are imported at the top of the file):

```python
class SwitchRoleView(APIView):
    """
    Switch the caller's ACTIVE role (single-account, multi-mode platform).

    Role is a mode, not an identity: users pick one on the role hub and can
    switch freely. Every user owns all four profiles, so no data is created or
    destroyed here — we only flip which mode their permissions resolve against.

    Permissions read `request.user.role` from the database (never from the raw
    JWT claim), so the switch takes effect immediately on the next request —
    no token refresh required.
    """

    permission_classes = [IsAuthenticated]

    SWITCHABLE = {
        UserRole.CUSTOMER,
        UserRole.ARTISAN,
        UserRole.EMPLOYER,
        UserRole.JOB_SEEKER,
    }

    def post(self, request):
        role = request.data.get("role")
        if role not in self.SWITCHABLE:
            return Response(
                {"detail": "Invalid role.", "code": "invalid_role"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        if user.role != role:
            user.role = role
            user.save(update_fields=["role"])
        return Response({"id": str(user.id), "role": user.role})
```

**`apps/accounts/api_urls.py`** — register the route alongside the other auth
paths (the frontend calls `POST /api/v1/auth/switch-role/`):

```python
    path("switch-role/", SwitchRoleView.as_view(), name="switch-role"),
```

...and add `SwitchRoleView` to the import from `.api_views`.

> Check the existing auth paths in this file. If they are declared WITHOUT an
> `auth/` prefix (e.g. `path("login/", ...)`) then the prefix is applied by the
> `include()` in `config/urls.py` — in that case use `path("switch-role/", ...)`
> exactly as above. If the paths already carry the prefix themselves
> (e.g. `path("auth/login/", ...)`), then use `path("auth/switch-role/", ...)`.

---

## 2. Make `role` optional at registration

Users no longer choose a role when signing up — they pick a mode on the hub
after verifying. In **`apps/accounts/api_serializers.py`**, find the register
serializer's `role` field and make it optional with a safe default:

```python
    role = serializers.ChoiceField(
        choices=UserRole.choices,
        required=False,
        default=UserRole.CUSTOMER,
    )
```

If `role` is currently in `Meta.fields` as a plain model field, declaring it
explicitly as above (above the `Meta` class) is enough to override it.

The default only decides which mode a brand-new user lands in first; they can
switch immediately from the hub.

---

## Deploy order

```bash
unzip -o jobcatch-phase1-hub.zip
# make the two edits above
python3 manage.py check
python3 manage.py migrate accounts
git add -A && git commit -m "Phase 1: single-account role hub (all profiles + switch-role)" && git push
```

Verify the backfill afterwards:

```bash
python3 manage.py shell -c "
from apps.accounts.models import User, CustomerProfile, ArtisanProfile, EmployerProfile, JobSeekerProfile
u = User.objects.count()
print('users', u)
for M in (CustomerProfile, ArtisanProfile, EmployerProfile, JobSeekerProfile):
    print(M.__name__, M.objects.count())
"
```

All four profile counts should equal the user count.
