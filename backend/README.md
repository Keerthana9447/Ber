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

Alembic is configured in `alembic.ini`. The baseline and invoice-workflow migrations and reference seed have been run against local PostgreSQL 16.15 databases. The first-admin command has not been run.

## Database verification status

Verified locally on PostgreSQL 16.15: the Alembic baseline and migrations through `0005_payments_credit_notes` applied, the seed ran idempotently, and live introspection found all mapped tables and columns. Database-backed auth flows and Phase 2b/3a/3b API, concurrency, rollback, payment, refund, credit-note, PDF, and integrity tests ran against the isolated test database.

**Still unverified:** current Phase 4/5 schema migrations and APIs against PostgreSQL, manual browser acceptance, production load/failover behavior, backups/restores, deployment configuration, and GST legal compliance. Consult a qualified Indian tax professional about GST assumptions, invoice numbering, and date rules before live use.

## Existing domain modules

| Module | Purpose |
|---|---|
| `berev_core/gst.py` | GST calculations, rounding, HSN summaries, and GSTIN validation |
| `berev_core/words.py` | Amount in words using lakh/crore grouping |
| `berev_core/numbering.py` | Financial-year invoice numbering and atomic sequence SQL |
| `berev_core/accounting.py` | Balanced double-entry posting and financial summaries |
| `berev_core/payments.py` | Payment allocation and invoice payment status |
