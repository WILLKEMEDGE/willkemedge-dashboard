# Backup & Disaster Recovery

**Status: UNVERIFIED. Every item in this document is a manual production check.**

Nothing in this repository can prove a backup exists, that it is recent, or that
it restores. `production.py` reads a `DATABASE_URL` and connects; that is the
entire extent of what the code knows about the database. Neon's marketing page
is not evidence, and neither is this file.

This matters more here than in most systems. The database is the **only** copy
of the books. Payments, arrears, the general ledger, KYC documents and the audit
trail exist nowhere else — there is no accounting package behind this, no
paper ledger being kept in parallel, and the bank's IPN feed has **no replay**
once Co-op's delivery retries are exhausted (`poll_bank_statement`, the intended
backfill, is a stub). If the database is lost, the books are lost.

---

## What the repository actually establishes

| Fact | Evidence |
|---|---|
| Postgres, reached over TLS | `production.py` — `dj_database_url.parse(..., ssl_require=True)` |
| Neon, pooled endpoint | `conn_health_checks=True`, `DISABLE_SERVER_SIDE_CURSORS=True`, and the error text naming the Neon dashboard |
| Migrations run on every deploy | `build.sh` — `manage.py migrate --noinput` |
| A failed migration aborts the deploy | `build.sh` — `set -o errexit`; Render keeps the previous release serving |
| Uploaded KYC documents are on the web service's local disk | `MEDIA_ROOT = BASE_DIR / "media"`, no object-storage backend configured |

**That last row is a finding, not a note.** Render's filesystem is ephemeral:
every deploy replaces the instance. Unless a persistent disk is attached, tenant
ID scans and KRA certificates uploaded since the last deploy are **already being
lost on each release**. Verify this before go-live (§2, item 6).

---

## 1. Backup verification — do these before go-live

Record the answer and the date beside each. "It's Neon, it's fine" is not an
answer.

| # | Check | How | Answer |
|---|---|---|---|
| 1 | Automated backups are on | Neon console → project → Backups / History | ☐ |
| 2 | Point-in-time recovery window | Neon console → Restore. Note the exact window in hours/days — the free plan's is much shorter than paid | ☐ ____ |
| 3 | Retention is ≥ 30 days | As above. Book-keeping errors are often found a month later, at reconciliation | ☐ |
| 4 | The plan will not silently expire | Billing → confirm the plan and payment method | ☐ |
| 5 | Someone other than the developer can reach the console | Neon → Members. A recovery that depends on one person's laptop is not a recovery plan | ☐ |
| 6 | KYC uploads survive a deploy | Deploy, then re-download a document uploaded before it (`/api/tenants/<id>/documents/<id>/download/`). If it 404s, attach a Render persistent disk or move `MEDIA_ROOT` to object storage **before go-live** | ☐ |
| 7 | An independent logical dump exists | `pg_dump` to storage outside Neon, on a schedule. A provider-managed snapshot does not protect against losing access to the provider account | ☐ |

## 2. Restore test — the only thing that proves a backup

A backup that has never been restored is a hypothesis.

1. In the Neon console, branch/restore the production database to a point ~1 hour
   in the past. **Restore to a new branch, never over production.**
2. Point a scratch environment at the restored branch:
   `DATABASE_URL=<restored branch URL>`.
3. Run the integrity checks against it — these are the questions that matter,
   not "does it connect":
   ```bash
   python manage.py check_db_invariants
   python manage.py check_data_integrity
   python manage.py migrate --check          # schema matches this release
   ```
4. Spot-check against known figures:
   - total non-void payments for last month equals the figure on the Reports
     page in production;
   - `GET /api/reports/trial-balance/` returns `is_balanced: true`;
   - a named tenant's balance matches their last statement.
5. Record: **the wall-clock time the whole exercise took.** That number is your
   RTO. Anything you did not measure, you do not know.
6. Delete the scratch branch.

**Re-run this test after any change to the migration chain.**

## 3. Targets — to be agreed, then measured, not assumed

| | Target | Measured | Notes |
|---|---|---|---|
| **RPO** (data you can afford to lose) | ☐ ____ | ☐ ____ | Bounded by Neon's PITR granularity. Rent payments arrive continuously via IPN, and a lost credit is money received with no record — the tenant has an M-Pesa message and the system does not |
| **RTO** (time to be serving again) | ☐ ____ | ☐ ____ | Measure it in §2, do not estimate it |

## 4. Emergency procedure

### The database is unavailable
1. Check Neon status and the compute's suspend state (it auto-suspends when idle;
   the first request after that is slow, not failed).
2. Check Render logs for connection-pool exhaustion.
3. **The IPN endpoint is the urgent part.** While the database is down, Co-op's
   POSTs fail. Co-op retries a limited number of times and then gives up, and
   there is no backfill — those credits are gone from the system permanently.
   If the outage will outlast the retry window, ask Co-op for a statement
   covering it and reconcile by hand via `manage.py reprocess_unmatched_ipn` and
   the Reconciliation page.

### Financial data was deleted or corrupted
1. **Do not attempt to repair production first.** Restore to a branch and
   compare — you cannot tell what was lost without a known-good copy.
2. Identify the window: `FinancialAuditLog` and `LoginAttempt` are append-only
   and will tell you who did what and when.
3. Restore to a branch at a point before the damage, diff the affected tables,
   and re-apply the legitimate activity that happened after it.
4. Correct production **through the application** — a void, a corrected charge —
   so the ledger and the audit trail record the repair. Do not UPDATE the books
   in psql.

### A migration damaged data
1. The deploy has already aborted (`set -o errexit`) and the previous release is
   still serving. There is no rush.
2. Every data migration in this project has a reverse operation
   (`0016_recompute_arrears_with_vat`, `0017_convert_legacy_void_payments`,
   `0018_arrears_check_constraints`, `0010_financial_invariant_preflight`).
   `migrate <app> <previous>` is available.
3. If the reverse is not clean, restore to a branch from before the deploy.

## 5. Who

| Role | Person | Contact |
|---|---|---|
| Owns the Neon account | ☐ | ☐ |
| Owns the Render account | ☐ | ☐ |
| Authorises a production restore | ☐ | ☐ |
| Second person who can do all of the above | ☐ | ☐ |

A single-owner recovery plan fails when that person is unreachable, which is
disproportionately likely to be exactly when it is needed.

## 6. Evidence log

| Date | Test | Result | RTO | By |
|---|---|---|---|---|
| | | | | |

**Empty means never verified.**
