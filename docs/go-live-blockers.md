# Go-Live Checklist — production readiness

Companion to [go-live-checklist.md](go-live-checklist.md) (deployment plumbing),
[go-live-apis.md](go-live-apis.md) (integrations) and
[backup-and-recovery.md](backup-and-recovery.md).

This file covers the security, database-integrity and financial-correctness work
from the production-readiness review. Items marked **CODE — DONE** are fixed in
this branch and covered by tests. Items marked **MANUAL** cannot be verified from
the repository and must be checked against production by a person.

---

## BLOCKERS — do not go live with any of these open

| # | Item | Status |
|---|---|---|
| B1 | Every write endpoint enforces a role server-side | **CODE — DONE** (`apps/accounts/permissions.py`; 224 tests in `test_endpoint_permissions.py`) |
| B2 | A new write endpoint cannot silently default to `IsAuthenticated` | **CODE — DONE** (`test_every_write_route_is_covered` enumerates the URLconf) |
| B3 | **Demote the production accounts.** `accounts/0005_backfill_user_roles` promoted *every* pre-existing user to `owner` so nobody was locked out on deploy. Until someone tightens them, the role model is installed but has no effect on real logins | **MANUAL** |
| B4 | CSV exports cannot execute in a spreadsheet | **CODE — DONE** (`apps/common/csv_safety.py`, `frontend/src/lib/exportSafety.ts`) |
| B5 | The report "PDF" print window cannot execute injected HTML | **CODE — DONE** (`escapeHtml` in `exportSafety.ts`) |
| B6 | Financial invariants enforced by PostgreSQL, not just by serializers | **CODE — DONE** (`config/db_invariants.py` + the constraint migrations) |
| B7 | **Run `manage.py check_db_invariants` against production BEFORE deploying.** The constraint migration refuses to run on data that breaks a rule — it will not silently rewrite an amount or a period. A failed migration aborts the deploy and leaves the previous release serving, so this is safe, but knowing in advance is better | **MANUAL** |
| B8 | The login audit trail is not readable by every account | **CODE — DONE** (`CanViewAuditLog`) |
| B9 | Confirm `COOP_IPN_ALLOWED_IPS` is set. Empty means the IP allowlist is off and the bearer token is the only gate | **MANUAL** |
| B10 | Confirm `DJANGO_SECRET_KEY` is ≥ 50 random characters. The app now refuses to boot otherwise — verify before deploying, not during | **MANUAL** |
| B11 | A backup has been restored and the restore verified | **MANUAL** — see [backup-and-recovery.md](backup-and-recovery.md) |
| B12 | KYC uploads survive a deploy (Render's disk is ephemeral) | **MANUAL** — see backup doc §1.6 |
| B13 | No production feature calls a missing endpoint | **CODE — DONE** (six implemented, Landlord Statement removed) |

## HIGH — fix before launch unless explicitly accepted

| # | Item | Status |
|---|---|---|
| H1 | Password change works and invalidates other sessions | **CODE — DONE** (`ChangePasswordView`) |
| H2 | Profile edit works and cannot escalate privilege | **CODE — DONE** (`ProfileUpdateSerializer`) |
| H3 | Tenant PII no longer sent to a third-party avatar CDN | **CODE — DONE** (`avatarFor` renders locally) |
| H4 | Security headers on the Vercel-served SPA | **CODE — DONE** (`vercel.json`) |
| H5 | **Pin `connect-src` in the CSP to the real API origin.** It currently allows `https:` because the API host is a build-time variable this file cannot see. One-line edit once the domain is fixed | **MANUAL** |
| H6 | `ProtectedError`/`IntegrityError` return 409, not a 500 with a traceback | **CODE — DONE** (`config/exception_handler.py`) |
| H7 | Portfolio-wide rent adjustment uses Decimal and refuses negatives | **CODE — DONE** |
| H8 | Deposit refund percentage bounded 0–100 | **CODE — DONE** (serializer + DB check) |
| H9 | Voided payments excluded from the dashboard's recent list | **CODE — DONE** |
| H10 | Every page reachable on mobile | **CODE — DONE** (`MobileNav`, with a test) |
| H11 | Expense-breakdown income is net of VAT, so it agrees with the P&L. It was summing the gross, making the expense ratio on that page 16% off for any month containing commercial rent | **CODE — DONE** |
| H12 | Concurrent writes to one arrears row are serialised (`select_for_update` in `_update_arrears`); a double-void writes one audit row, not two | **CODE — DONE** |
| H13 | **Rotate `CRON_TRIGGER_TOKEN` if it has ever been used in a `?token=` URL.** Query strings land in access logs, proxy logs and `Referer` headers. Prefer the `Authorization` header; the query form exists only for cron services that cannot send one | **MANUAL** |
| H14 | **Confirm `ADMIN_ALERTS_ENABLED` and `TENANT_NOTIFICATIONS_ENABLED` are `true` in production.** Both default to true, but the local `.env` sets them false; a copied env file silences receipts, reminders and the unmatched-credit alert | **MANUAL** |

## MEDIUM — safe to follow immediately after launch

| # | Item |
|---|---|
| M1 | Turn pagination on by default. `LimitOffsetPagination` is wired and opt-in (`?limit=`), which breaks nothing today; making it the default changes every list response to `{count, results}` and must ship with the frontend in the same release |
| M2 | `adjust-rent` changes `Unit.monthly_rent` but billing reads `Tenant.monthly_rent`, so it does not re-price sitting tenants. The response now says so explicitly. Decide whether a per-tenant rent-review flow is wanted |
| M3 | Add a theme toggle. The full dark palette and the boot script exist; nothing writes `localStorage["willkemedge-theme"]` |
| M4 | Expose `payment_method` on `ExpenseSerializer` so petty cash (GL 1010) is selectable — the model, the account and the Accounting tab all exist, only the serializer field is missing |
| M5 | Give `Budget` an API or admin registration; rows can currently only be created in a shell |
| M6 | Implement `poll_bank_statement`, or accept and document the risk that IPN deliveries lost past Co-op's retry window are unrecoverable |
| M7 | Split `ReportsPage.tsx` (826 lines, 16 tabs) and `BuildingsPage.tsx` (1153 lines, 4 modals) |
| M8 | Add an uptime monitor on `/api/health/` |

## LOW — technical debt

| # | Item |
|---|---|
| L1 | Delete the untracked scratch files `backend/_inspect_periods.py`, `backend/_run_inspect.bat`, `backend/apps/payments/management/commands/_inspect_periods.py`. They fail `ruff check .` and would break CI the moment anyone runs `git add .` |
| L2 | The README describes M-Pesa Daraja, SendGrid, AWS S3 and Redis. None are used; `docs/go-live-apis.md` contradicts it explicitly |
| L3 | Remove `weasyprint`, `django-ratelimit`, `africastalking` (backend) and `framer-motion`, 5 × `@radix-ui/*` (frontend) — all declared, none imported |
| L4 | Remove dead code: `views_reports_header.py`, `celery_app.beat_schedule`, `post_petty_cash_topup`, `post_deposit_refund`, `receipt_pdf.html`, `PaymentReceipt.tsx`, `useMockPayment` |
| L5 | Resolve `frontend/src/lib/displayName.ts`, which rewrites "sharon"→"wilkem" in every rendered name and email |
| L6 | Reconcile the three schedule definitions (`celery_app.py`, the `cron_views` docstring, `scheduled-jobs.yml`). Only the workflow is real, and the docstring disagrees with it about `recalculate-statuses` |
| L7 | Two `formatKES` implementations with different output (`lib/money.ts`, `lib/taxService.ts`) |

---

## Deploy-day order

1. `manage.py check_db_invariants` **against production** (B7). Do not proceed
   until it is clean.
2. Confirm B9, B10, H13 and H14 in the Render environment.
3. Deploy. `build.sh` runs `collectstatic` then `migrate`. The migration chain
   runs the invariant pre-flight before it touches the schema; if it fails, the
   deploy aborts and the previous release keeps serving.
4. `manage.py check_data_integrity` — the existing financial-invariant sweep.
5. `GET /api/reports/trial-balance/` → confirm `is_balanced: true`.
6. Demote the production accounts (B3) and confirm with a real login per role.
7. Smoke-test one payment end to end: record it, check the receipt, check the
   ledger entry, then void it and confirm the reversal.
