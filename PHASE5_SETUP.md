# JobCatch: Phase 1 updates + Phase 2 features — setup & deployment

This release adds employer KYC, paid job listings, paid artisan verification,
bill payments with a wallet (VTU.ng), job seeker Pro with WhatsApp alerts and
in-app upgrade prompts, plus the four Phase 1 fixes. Everything is wired into
the existing apps; no manual code edits are needed after copying the files in.

---

## 1. Deploy order (Render)

1. Push **jobcatch-backend** first. `build.sh` runs `migrate`, which:
   - adds invoice numbers to every existing payment (historical successful
     payments are marked as already fulfilled, so nothing is re-delivered);
   - keeps every existing job live with its real posting date and no expiry;
   - gives employers already CAC-verified a **verified KYC case** (marked
     "grandfathered"), so they keep their badge and can keep posting;
   - seeds starter job packages and upgrade prompts (both editable in admin).
2. Set the new environment variables (section 3) on the **jobcatch-api** service.
3. Create the **jobcatch-scheduler** cron job (it's in `render.yaml`; with a
   Blueprint, "Sync" creates it). Give it the same secrets as the web service.
4. Push **jobcatch-web**.
5. Configure webhooks (section 4).

> Heads-up: with `KYC_REQUIRED_TO_POST_JOBS=True` (default), employers who were
> **not** previously verified can't post new jobs until they complete KYC, and
> with `PAID_JOB_LISTINGS_ENABLED=True` new jobs stay drafts until paid for.
> Set either to `False` to roll the change out gradually. Existing live jobs
> are unaffected.

## 2. What the scheduler does (`python manage.py run_scheduled_tasks`)

Runs every 15 minutes; each step is idempotent and isolated:

| Step | What it does |
|---|---|
| reconcile_payments | Re-verifies checkouts the webhook missed; re-runs failed fulfilment; closes checkouts abandoned > 48 h |
| requery_bill_orders | Asks VTU.ng about orders still "processing"; refunds to wallet if the biller failed them |
| expire_job_listings | Closes jobs past their listing period and notifies the employer; expires unused credit packs |
| expire_subscriptions | Ends lapsed Premium/Pro/Seeker Pro plans and removes the featured flag |
| renewal_reminders | One "ends in 3 days" reminder per plan |
| upgrade_nudges | Weekly in-app + email nudge (Seeker Pro / artisan verification / upgrade), max once a week each |

Expiry is also enforced at query time, so a late cron never shows a stale job.

## 3. Environment variables

All are optional with safe defaults. See `.env.example` for the full list.

| Variable | Default | Notes |
|---|---|---|
| `FLUTTERWAVE_SECRET_KEY` | — | Enables Flutterwave at checkout |
| `FLUTTERWAVE_WEBHOOK_HASH` | — | The "secret hash" you set in the Flutterwave dashboard |
| `DEFAULT_PAYMENT_GATEWAY` | `paystack` | Used when the user doesn't pick one |
| `FRONTEND_URL` | `https://jobcatchonline.com` | Links in WhatsApp alerts |
| `PAID_JOB_LISTINGS_ENABLED` | `True` | `False` = jobs publish free and never expire |
| `KYC_REQUIRED_TO_POST_JOBS` | `True` | |
| `KYC_AUTO_APPROVE` | `False` | `True` = verify instantly when CAC, TIN and NIN lookups all pass |
| `KYC_PROVIDER` | `manual` | `dojah` for live CAC/TIN/NIN lookups |
| `DOJAH_APP_ID`, `DOJAH_SECRET_KEY` | — | From the Dojah dashboard (use `DOJAH_BASE_URL=https://sandbox.dojah.io` to test) |
| `SMS_BACKEND` | `console` | **Set to `termii` in production**, or phone OTPs are only logged |
| `TERMII_API_KEY`, `TERMII_SENDER_ID` | — | Sender ID must be approved by Termii |
| `ARTISAN_VERIFICATION_FEE` | `5000` | Naira |
| `SEEKER_PRO_PRICE` / `SEEKER_PRO_PERIOD_DAYS` | `3000` / `30` | |
| `WHATSAPP_BACKEND` | `console` | `cloud_api` to send real WhatsApp alerts |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | — | Meta WhatsApp Cloud API |
| `WHATSAPP_JOB_ALERT_TEMPLATE` | `job_alert` | Approved template with 3 body variables: title, company, link |
| `VTU_USERNAME`, `VTU_PASSWORD` | — | VTU.ng **reseller** account login |
| `VTU_USER_PIN` | — | Used to verify VTU.ng webhook signatures |
| `BILL_FEE_AIRTIME` … `BILL_FEE_TV` | `0` | Optional flat convenience fee per purchase |
| `PRIVATE_MEDIA_ROOT` | `<RENDER_DISK_PATH>/_private` | KYC documents; never served by `/media/` |

## 4. Webhooks & external dashboards

| Where | URL / setting |
|---|---|
| Paystack → Settings → API Keys & Webhooks | `https://<api-domain>/api/v1/payments/webhook/paystack/` |
| Flutterwave → Settings → Webhooks | `https://<api-domain>/api/v1/payments/webhook/flutterwave/` and set the **Secret hash** = `FLUTTERWAVE_WEBHOOK_HASH` |
| VTU.ng → Account → Developer | Webhook `https://<api-domain>/api/v1/billing/webhook/vtu/`; enable API access; optionally whitelist Render's outbound IPs; set a low-balance alert |
| Paystack/Flutterwave redirect | `PAYMENT_CALLBACK_URL` (unchanged): `https://jobcatchonline.com/payments/callback` |

**Fund your VTU.ng wallet.** Bill purchases are paid from your VTU.ng reseller
balance; if it runs out, purchases fail and customers are refunded to their
JobCatch wallet automatically. Watch the balance in VTU.ng.

## 5. Admin

- **In-app review dashboard:** `/admin/reviews` (log in as an admin user):
  employer KYC and artisan verification queues, document viewer, automated
  check results, approve/reject/revoke/override, full audit history.
- **Django admin** additions: Employer KYC cases, Job packages (prices/credits/
  duration editable inline), Package purchases, Bill orders (requery action),
  Wallets & ledger (read-only), Upgrade prompts (copy, CTA, cooldown; shows
  shown/clicked/dismissed counts), Job alert subscriptions, Payments (with the
  event timeline and a "re-verify with gateway" action).

## 6. Local development

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8080
python manage.py run_scheduled_tasks        # run the scheduler once
pytest                                      # all pass except 4 pre-existing AI-chatbot tests
```

With no keys set, KYC lookups are "manual", SMS/WhatsApp print to the console,
and bill purchases are refunded with "not configured".

## 7. Notes & known limits

- **Dojah endpoints**: CAC uses `/api/v1/kyc/cac/basic`, TIN `/api/v1/kyc/tin`,
  NIN `/api/v1/kyc/nin`. The NIN path couldn't be confirmed against Dojah's current
  reference, so all three are overridable with `DOJAH_*_PATH`. Test in the Dojah
  sandbox before setting `KYC_PROVIDER=dojah`.
- **Director NIN** is stored in the database in plain text (visible only to
  admins, masked for the employer). Add field-level encryption if your
  compliance needs require it.
- Plans (Premium/Pro/Seeker Pro) are one-off 30-day payments; there is no
  automatic card re-billing. Renewing early stacks the days.
- The legacy "Verifications" document flow and `/admin/job-videos/` still work.
  The **Verified Artisan** badge is now granted by the paid application review.
- Existing documents uploaded through the old Verification flow remain in public
  `/media/verifications/`; new KYC and artisan documents use private storage.
