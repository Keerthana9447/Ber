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

The API is versioned under `/api/v1`; `/api/v1/health` is an application-liveness endpoint and does not check database connectivity. Auth, customer CRUD, and the dashboard endpoints are available; other business module APIs are still being implemented. The app does not create database tables on startup.
New PostgreSQL connections use a five-second connect timeout. SQLAlchemy operational failures return HTTP 503 with a `Retry-After: 5` header; this does not replace database availability monitoring.

Run database-independent and existing tests from `backend`:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

After a real PostgreSQL environment is available and the schema baseline has been validated, the interactive first-admin command is:

```powershell
..\.venv\Scripts\python.exe -m app.cli create-admin
```

It prompts for email, name, and a password (twice); no default account or password is supplied. The reference seed entry point is `python -m app.seed`; it has not been run.

`scripts/generate_models.py` generates the SQLAlchemy 2.x declarative mappings from `db/schema.sql`. Check that generated source is current with:

```powershell
..\.venv\Scripts\python.exe scripts\generate_models.py --check
```

Alembic is configured in `alembic.ini`. In the current local setup, the baseline migration and reference seed have been run against `berev_dev` and `berev_test` on PostgreSQL 16.15. The first-admin command has not been run.

## Database verification status

Verified locally on PostgreSQL 16.15: the Alembic baseline applied to both local databases, the seed ran idempotently, and live introspection found all 49 mapped tables and their columns. A database-backed smoke test exercised login, profile, refresh rotation, logout, and refresh-token revocation.

**Still unverified:** PostgreSQL-specific numbering concurrency, production load/failover behavior, backups/restores, deployment configuration, and GST legal compliance. The existing numbering concurrency and rollback tests use SQLite and do not verify PostgreSQL behavior. Consult a qualified Indian tax professional about GST assumptions and calculations.

## Existing domain modules

| Module | Purpose |
|---|---|
| `berev_core/gst.py` | GST calculations, rounding, HSN summaries, and GSTIN validation |
| `berev_core/words.py` | Amount in words using lakh/crore grouping |
| `berev_core/numbering.py` | Financial-year invoice numbering and atomic sequence SQL |
| `berev_core/accounting.py` | Balanced double-entry posting and financial summaries |
| `berev_core/payments.py` | Payment allocation and invoice payment status |
