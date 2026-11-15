# Phase 3 backend edits

The zip includes `apps/accounts/migrations/0008_employer_contact_fields.py`
(drop in as-is). Two hand-edits are needed so Django and DRF actually know
about the new columns.

---

## 1. `apps/accounts/models.py` — add the fields to `EmployerProfile`

Find the `EmployerProfile` class and add these three fields (keep the existing
`company_name`, `cac_number`, `is_cac_verified`, `website` as they are):

```python
    address = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
```

These must match migration `0008` exactly, or `makemigrations` will want to
generate another migration.

---

## 2. `apps/accounts/api_serializers.py` — expose them on `/me/profile/`

Find `EmployerProfileSerializer` and add the three names to `Meta.fields`:

```python
        fields = (
            ...,               # keep whatever is already listed
            "company_name", "cac_number", "is_cac_verified", "website",
            "address", "contact_email", "phone",
        )
```

Make sure `is_cac_verified` stays read-only (employers must not be able to mark
themselves verified). If there is a `read_only_fields` tuple, leave it alone —
just don't add the new three to it, since employers edit those.

---

## Deploy

```bash
unzip -o jobcatch-phase3-backend.zip
# make the two edits above
python3 manage.py check
python3 manage.py migrate accounts
git add -A && git commit -m "Phase 3: employer company profile fields" && git push
```

Verify:

```bash
python3 manage.py shell -c "
from apps.accounts.models import EmployerProfile
f = {x.name for x in EmployerProfile._meta.get_fields()}
print('address', 'address' in f, '| contact_email', 'contact_email' in f, '| phone', 'phone' in f)
"
```

All three should print `True`.
