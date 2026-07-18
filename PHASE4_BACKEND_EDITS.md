# Phase 4 backend wiring — paid CV service

The zip ships a complete new Django app, `apps/cvservice`, with its own models,
admin screens, serializers, views, URLs and initial migration. Nothing inside it
needs editing. Four small wiring edits connect it to the project.

---

## 1. Install the app

**`config/settings/base.py`** — add to `LOCAL_APPS` (or whatever list holds
`apps.jobs`, `apps.payments`, ...):

```python
    "apps.cvservice",
```

While you're in this file, add the price (kept in settings so it can change
without a code deploy):

```python
CV_SERVICE_PRICE = env.int("CV_SERVICE_PRICE", default=5000)  # Naira, one-off
```

If this settings file doesn't use `django-environ`'s `env`, just use:

```python
CV_SERVICE_PRICE = 5000
```

---

## 2. Mount the routes

**`config/urls.py`** — alongside the other `/api/v1/` includes:

```python
    path("api/v1/", include("apps.cvservice.urls")),
```

Put it next to however `apps.jobs` / `apps.accounts.api_urls` are included, so
it lands on the same `/api/v1/` prefix.

---

## 3. Let Paystack pay for it

**`apps/payments/models.py`** — add a purpose to `PaymentPurpose`:

```python
    CV_SERVICE = "cv_service", "CV Service"
```

Then run `python3 manage.py makemigrations payments` (it's a choices change, so
the migration is trivial) and commit the generated file.

**`apps/payments/views.py`** (wherever `initialize` decides the amount) — the
initialize endpoint must know what a `cv_service` payment costs. Find where it
resolves the amount for each purpose and add:

```python
        if purpose == PaymentPurpose.CV_SERVICE:
            amount = settings.CV_SERVICE_PRICE
```

---

## 4. Grant access when payment succeeds

**`apps/payments/tasks.py`** — inside `process_successful_payment`, after the
payment is confirmed successful, add:

```python
    if payment.purpose == PaymentPurpose.CV_SERVICE:
        from django.utils import timezone
        from apps.cvservice.models import CVServiceAccess

        access, _ = CVServiceAccess.objects.get_or_create(user=payment.payer)
        if not access.is_active:
            access.is_active = True
            access.paid_at = timezone.now()
            access.payment_reference = payment.reference
            access.save(update_fields=["is_active", "paid_at", "payment_reference"])
```

This is the only place access is granted, so a seeker can never unlock the
service without a confirmed Paystack payment.

---

## Deploy

```bash
unzip -o jobcatch-phase4-backend.zip
# make the four edits above
python3 manage.py makemigrations payments   # for the new PaymentPurpose choice
python3 manage.py check
python3 manage.py migrate
git add -A && git commit -m "Phase 4: paid CV service + admin forwarding" && git push
```

---

## How the admin forwards a CV

1. Go to `/admin/` → **CV Service** → **CV submissions**
2. Open a submission (the CV file has an "Open CV" link)
3. In the **CV referrals** inline at the bottom, pick an **employer**, add an
   optional note, and save
4. Optionally set the submission's status to *Forwarded*

The employer immediately sees it in their dashboard under **Referred CVs**.

> The employer dropdown lists user accounts. It uses autocomplete, so type the
> company's email to find them. Note it searches the `User` model — for the
> autocomplete to work, `UserAdmin` must define `search_fields`. If the
> dropdown errors, add `search_fields = ("email", "full_name")` to your
> `UserAdmin` in `apps/accounts/admin.py`.
