# BEREV ERP backend

The backend combines the existing pure-Python domain core with a FastAPI application foundation.

## Local setup

From the project root, copy `.env.example` to `.env` and set local values there. Do not commit `.env` or put real credentials in source code.

Install the exact dependency versions recorded in `backend/requirements.txt`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Run the API from `backend` after configuring `DATABASE_URL` and a random `JWT_SECRET_KEY` in the root `.env`:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory
```

The API is versioned under `/api/v1`; `/api/v1/health` is an application-liveness endpoint and does not check database connectivity. Implemented workflows include authentication, dashboard, settings, customers, products/inventory, invoice drafts through issue/cancel/PDF, customer receipts and advances, payment allocation/reversal/refunds, credit notes, customer and supplier ledgers, quotations, sales orders, delivery challans, order-to-invoice drafts, sales reports, PDFs, opening inventory valuation, supplier/purchase-request/order workflows, partial GRNs, supplier invoices/payments, purchase returns, input-GST and valuation reports, and expense submission/approval/receipt upload/accounting posting. Purchasing is backend-only at this point; purchase exports and return reversal remain incomplete. Dealer workflows, full accounting/report administration, notification scheduling, user/password-reset management, and production operations remain incomplete. The app does not create database tables on startup.

Gateway receipts are disabled unless `GATEWAY_WEBHOOK_SECRET` is configured. The webhook verifies an HMAC-SHA256 signature; connect a real payment provider only after confirming its signing format and operational requirements.
New PostgreSQL connections use a five-second connect timeout. SQLAlchemy operational failures return HTTP 503 with a `Retry-After: 5` header; this does not replace database availability monitoring.

Run the backend tests from the project root. PostgreSQL integration tests read `BEREV_TEST_DATABASE_URL` or `TEST_DATABASE_URL` (the latter may come from the root `.env`) and reject any database except `berev_test`. They never fall back to `DATABASE_URL`.

```powershell
$env:REQUIRE_DB_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

CI must set `REQUIRE_DB_TESTS=1` and configure `BEREV_TEST_DATABASE_URL` to an isolated PostgreSQL database named exactly `berev_test`; missing configuration or an unavailable database fails instead of silently skipping database tests. The CI acceptance command must report zero skipped tests.

The integrity check reports PASS/FAIL for invoice numbering, stock ledger, and accounting invariants. Point `DATABASE_URL` at the test database for this command; never use the development database for acceptance verification:

```powershell
$env:DATABASE_URL = $env:BEREV_TEST_DATABASE_URL
..\.venv\Scripts\python.exe -m app.cli verify-integrity
```

The interactive first-admin command is:

```powershell
..\.venv\Scripts\python.exe -m app.cli create-admin
```

It prompts for email, name, and a password (twice); no default account or password is supplied. The reference seed entry point is `python -m app.seed`.

`scripts/generate_models.py` generates the SQLAlchemy 2.x declarative mappings from `db/schema.sql`. Check that generated source is current with:

```powershell
..\.venv\Scripts\python.exe scripts\generate_models.py --check
```

Alembic revision `0001_schema_baseline` applies the frozen DDL snapshot in `alembic/versions/0001_schema_baseline.sql`; it does not read the current `db/schema.sql` or live ORM models. Follow-up revisions apply their own changes. The strict migration test upgrades an empty schema, compares the resulting PostgreSQL catalog against ORM metadata, verifies expected triggers, and exercises downgrade to base and upgrade to head. Downgrading revision `0005_payments_credit_notes` is blocked if its added snapshot data is populated.

## Database verification status

Verified locally on PostgreSQL 16.15 at `127.0.0.1:5432`: fresh `berev_dev` and `berev_test` databases owned by the non-superuser `berev_app` migrated to head; the reference seed ran on `berev_dev` only. The strict full backend suite passed **75 tests with zero skips or errors** against `berev_test`. All eight `verify-integrity` checks passed. Coverage includes Phase 2b through Phase 6 database-backed workflows, including authentication/lockout, dealer isolation, receipts, purchasing/valuation, and sales documents.

**Still unverified:** manual browser acceptance, production load/failover behavior, backups/restores, deployment configuration, and GST/accounting legal review. Consult a qualified Indian tax professional about GST assumptions, invoice numbering, and date rules before live use.

## Existing domain modules

| Module | Purpose |
|---|---|
| `berev_core/gst.py` | GST calculations, rounding, HSN summaries, and GSTIN validation |
| `berev_core/words.py` | Amount in words using lakh/crore grouping |
| `berev_core/numbering.py` | Financial-year invoice numbering and atomic sequence SQL |
| `berev_core/accounting.py` | Balanced double-entry posting and financial summaries |
| `berev_core/payments.py` | Payment allocation and invoice payment status |

## Startup preflight, readiness and staging validation

- On startup the app checks that the database is reachable and that its Alembic revision equals this version's migration head. `STARTUP_PREFLIGHT=off|warn|strict` controls it; the default is `strict` when `ENVIRONMENT=production` (the app refuses to start), `warn` in development (logs the problem), `off` in tests. Run the same check by hand with `python -m app.cli preflight`.
- `GET /api/v1/health` is liveness only. `GET /api/v1/health/ready` checks the database and migrations and returns 503 with short problem codes (details go to the server log); point load balancers and monitors at it.
- The notification scheduler now logs the exception type, failure count and retry delay (full traceback on the first failure and every 10th), backs off exponentially up to 15 minutes, and logs a recovery line. `/health/ready` reports `notification_scheduler_failing`.
- `python scripts/staging_validate.py --base-url https://staging.example.com --db-checks` runs a black-box check of a running staging API (see its header for environment variables). See `../docs/PRODUCTION_READINESS.md` for everything it does not cover.
