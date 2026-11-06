# BEREV ERP Roadmap

## Current baseline

- The backend contains stdlib-only domain modules for GST calculations, amount-in-words, invoice numbering, accounting, and payment allocation.
- Existing unit tests cover these modules; the invoice-number concurrency tests use SQLite.
- `backend/db/schema.sql` is a PostgreSQL draft with 49 tables and integrity triggers. It has not yet been executed against PostgreSQL and is not verified.
- The repository does not yet contain a FastAPI application, SQLAlchemy models, Alembic migrations, authentication endpoints, frontend, or local development configuration.

## Phase 1 — Architecture, database, backend foundation, and authentication

1. Establish the application structure and environment-based configuration.
2. Provision isolated PostgreSQL databases `berev_dev` and `berev_test` on a local scratch instance; keep credentials in a git-ignored `.env` and commit only placeholders in `.env.example`.
3. Execute `backend/db/schema.sql` against `berev_dev` only. Fix execution errors surgically and record every schema error and fix in `CHANGES.md`.
4. Verify the schema only after a clean execution on real PostgreSQL. Convert the verified baseline into Alembic-managed migrations.
5. Add a FastAPI application, SQLAlchemy persistence layer, health endpoint, and initial authentication/user/role support using secure password hashing and JWTs.
6. Add automated backend tests that use `berev_test`; never use production or development data for tests.

**Exit criteria:** schema executes cleanly on PostgreSQL 16; the verification result is recorded; Alembic can inspect/manage the baseline; application configuration and auth foundation are tested against the test database.

**Status:** Database-independent Phase 1 foundation is implemented. PostgreSQL is not installed in the current workspace; schema execution, live model comparison, migration, seed, database API tests, and PostgreSQL concurrency validation remain **UNVERIFIED**.

## Phase 2 — Dashboard, parties, products, and inventory

- Implement database-backed dashboard metrics, customer and supplier management, product/category masters, EV serialization, warehouses, stock balances, and auditable stock movements.
- Enforce branch/dealer scope and prevent negative stock unless an authorized configuration explicitly allows it.
- Test inventory operations and relevant permissions against `berev_test`.

## Phase 3 — Sales, purchasing, GST billing, and invoices

- Implement quotations, sales orders, delivery challans, purchases, goods receipts, invoice lifecycle, configurable tax classification, and professional invoice output.
- Make invoice numbering, invoice posting, and stock updates transactional and safe under concurrent requests.
- Test intra-state/inter-state tax, issuance, cancellation, and inventory effects.

## Phase 4 — Payments, expenses, dealer management, and accounting

- Implement receipts, supplier payments, advances, allocations, outstanding balances, expenses, dealer workflows, balanced journal posting, reversals, and accounting reports.
- Preserve audit history for posted financial records and test end-to-end workflows.

## Phase 5 — Reports, notifications, settings, and audit

- Implement database-backed operational and financial reports with date/business filters and exports.
- Add configurable notifications, company/tax settings, and audit coverage for sensitive business operations.
- Keep external messaging disabled unless explicitly configured and authorized.

## Phase 6 — Quality, security, performance, deployment, and handover

- Complete backend/API, frontend, and critical workflow tests; review authorization and data isolation.
- Optimize measured bottlenecks, complete deployment and backup guidance, and document setup, migrations, admin creation, and operational procedures.
- Confirm each acceptance criterion from `BEREV_MASTER_PROMPT.md` with evidence; do not describe unimplemented features as complete.

## Operating constraints

- Keep business credentials and secrets out of source, Compose files, and documentation.
- Use a scratch database for schema validation, `berev_dev` for development schema work, and `berev_test` for tests.
- Do not install software or download images without user approval.
- Do not mark the PostgreSQL schema verified until the exact schema runs cleanly on a real PostgreSQL instance.
- Configure official company/tax details rather than inventing them; tax/legal behavior requires qualified professional review.
