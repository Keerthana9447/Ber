# Changes

## 2026-11-06 — Phase 1 setup

- Added `ROADMAP.md` describing the implementation phases and verification gates.
- Added `KNOWN_GAPS.md` recording the current implementation and validation gaps.
- Confirmed all 30 existing backend unit tests pass using the project's local virtual environment.
- Checked for Docker and PostgreSQL availability. `docker` is not on PATH; Docker Desktop is not installed at its standard Windows path; PostgreSQL commands/service and a PostgreSQL 16 installation were not found.
- Phase 1 schema execution is blocked: no scratch PostgreSQL instance or `berev_dev` / `berev_test` databases are available. No database credentials were generated and no `.env` was created.
- `backend/db/schema.sql` has **not** been executed. No schema execution errors have been observed or fixed, and the schema remains unverified.
- Waiting for installation and startup of Docker Desktop for Windows (preferred) or PostgreSQL 16 for Windows. No software was downloaded or installed.

## 2026-11-06 — PostgreSQL Phase 1 continuation

- Added a root `.gitignore` that excludes `.env` and Python-generated files while allowing `.env.example`.
- Added `.env.example` with placeholders for PostgreSQL, application/test connection URLs, and JWT configuration. No real credential or generated password is present.
- The user reports PostgreSQL 16 is installed and the admin password is in the project-root `.env`. In the current workspace, that `.env` is absent, the specified `C:\Program Files\PostgreSQL\16\bin\psql.exe` path does not exist, no PostgreSQL Windows service was found, and port 5432 has no visible listener.
- The user subsequently clarified PostgreSQL is not installed yet; no PostgreSQL setup was attempted.
- As a result, no role or database was created and `backend/db/schema.sql` was not run. No schema errors were observed or fixed; the schema remains unverified. No credentials were printed, logged, or generated.
- Phase 1 database setup remains blocked until the PostgreSQL installation and secret file are accessible from this workspace.

## 2026-11-06 — Database-independent Phase 1 foundation

- Installed the backend dependencies into the existing `.venv` and pinned the resolved versions in `backend/requirements.txt`.
- Added the FastAPI app factory, validated environment configuration, JSON structured request logging, versioned `/api/v1` router, and liveness-only health endpoint.
- Added SQLAlchemy 2.x declarative mappings for all 49 schema tables and a deterministic generator from `backend/db/schema.sql`; the generator and ORM metadata load without opening a database connection.
- Added Alembic configuration and a baseline revision that applies the canonical SQL when deliberately run later. No migration was generated against or run on any database.
- Added Argon2 password hashing, signed access/refresh JWT helpers, hashed refresh-token persistence, permission dependencies, lockout transition logic, login/refresh/logout/profile routes, and an interactive first-admin CLI with no default credentials.
- Added an idempotent reference-seed script for the existing state and chart-of-accounts constants, plus units, payment methods, roles, and permissions. It has not been run.
- Added database-independent auth, config, app-liveness, and model-source generation tests.
- Validation: all 50 backend unit tests passed. This includes the 30 existing tests; numbering concurrency remains SQLite-only and is not PostgreSQL verification.
- **UNVERIFIED:** execution of `schema.sql`, model compatibility with a live PostgreSQL catalog, Alembic baseline behavior, seed behavior, any database-touching API test, and PostgreSQL concurrency. No PostgreSQL database was available or used.

## 2026-11-06 — Local PostgreSQL and reliability verification

- With user approval, downloaded the official PostgreSQL 16.15 Windows binary archive and extracted it under the current user's local profile. No system-wide service was installed; the local cluster is started with `pg_ctl`.
- Created fresh local `berev_dev` and `berev_test` databases. Random local credentials are stored in the ignored root `.env`; no values are recorded here.
- Applied the Alembic baseline and ran the reference seed against both databases. Live introspection confirmed all 49 mapped tables and their columns.
- Fixed the seed script's invalid `AccountModel` import to use the generated `LedgerAccount` mapping.
- Exercised PostgreSQL-backed login, profile, refresh rotation, logout, and token revocation. Test accounts were disabled after the smoke test; append-only login audit rows were retained.
- Added a five-second PostgreSQL connection timeout and an HTTP 503 response with `Retry-After` when SQLAlchemy reports an operational database failure.
- Validation: 51 backend tests pass, generated models are current, 16 frontend tests pass, and the frontend production build succeeds.
- **Still unverified:** PostgreSQL concurrency, first-admin creation, production load/failover, deployment, and backup/restore behavior.
- Added a PostgreSQL connection timeout and structured HTTP 503 response for database operational failures; verified unavailable-database login returns promptly.
- Fixed the Vite config import so the frontend typecheck, tests, and build run successfully.
- Added a frontend lockfile, upgraded vulnerable router/validation/build/test dependencies, and split chart code into its own production chunk. `npm audit` reports zero vulnerabilities.
- No shared/default administrator password is configured. Create the first admin interactively with `python -m app.cli create-admin`; use a unique strong password.
