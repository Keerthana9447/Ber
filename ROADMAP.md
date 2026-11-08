# BEREV ERP Roadmap

## Current baseline

- The backend contains stdlib-only domain modules for GST calculations, amount-in-words, invoice numbering, accounting, and payment allocation.
- Existing unit tests cover these modules; the invoice-number concurrency tests use SQLite.
- `backend/db/schema.sql` currently defines 50 tables and integrity triggers. The Alembic baseline (49 tables, before Phase 4) has been applied to fresh local PostgreSQL 16.15 development and test databases; Phase 4 adds one table and is not yet verified against PostgreSQL.
- A FastAPI app, generated SQLAlchemy mappings, Alembic baseline, authentication foundation, and React/TypeScript frontend are present. The PostgreSQL schema and baseline are verified locally; production operations remain unverified.

## Phase 1 — Architecture, database, backend foundation, and authentication

1. Establish the application structure and environment-based configuration.
2. Provision isolated PostgreSQL databases `berev_dev` and `berev_test` on a local scratch instance; keep credentials in a git-ignored `.env` and commit only placeholders in `.env.example`.
3. Execute `backend/db/schema.sql` against `berev_dev` only. Fix execution errors surgically and record every schema error and fix in `CHANGES.md`.
4. Verify the schema only after a clean execution on real PostgreSQL. Convert the verified baseline into Alembic-managed migrations.
5. Add a FastAPI application, SQLAlchemy persistence layer, health endpoint, and initial authentication/user/role support using secure password hashing and JWTs.
6. Add automated backend tests that use `berev_test`; never use production or development data for tests.

**Exit criteria:** schema executes cleanly on PostgreSQL 16; the verification result is recorded; Alembic can inspect/manage the baseline; application configuration and auth foundation are tested against the test database.

**Status:** The local PostgreSQL schema, baseline migration, reference seed, model/catalog match, and core auth flow have been verified. Phase 3a invoice lifecycle and numbering concurrency have also been exercised against the isolated PostgreSQL test database. First-admin creation, production operations, and deployment validation remain **UNVERIFIED**.

## Phase 2 — Dashboard, parties, products, and inventory

- Implement database-backed dashboard metrics, customer and supplier management, product/category masters, EV serialization, warehouses, stock balances, and auditable stock movements.
- Enforce branch/dealer scope and prevent negative stock unless an authorized configuration explicitly allows it.
- Test inventory operations and relevant permissions against `berev_test`.

**Status:** Dashboard, customers, products, inventory foundations, company settings/readiness, invoice drafts/previews, invoice lifecycle, payments, refunds, credit notes, and customer statements are implemented. Phase 4 adds quotations, sales orders, delivery challans, sales reports, and PDF output. Purchases, expenses, and production operations remain future work.

## Phase 3 — Sales, purchasing, GST billing, and invoices

- Implement quotations, sales orders, delivery challans, purchases, goods receipts, invoice lifecycle, configurable tax classification, and professional invoice output.
- Make invoice numbering, invoice posting, and stock updates transactional and safe under concurrent requests.
- Test intra-state/inter-state tax, issuance, cancellation, and inventory effects.

**Status:** Phase 3a implements draft -> approved -> issued -> cancelled for tax invoices and bills of supply; proformas remain draft-only and can be copied into a new tax-invoice draft. Phase 3b implements customer receipts/advances, allocation, refunds and reversals, credit notes with optional restock, customer statements, and stored-value PDFs. Earlier Phase 3b evidence: 58 backend tests passed on PostgreSQL, 21 frontend tests passed, TypeScript/build passed, and all eight integrity checks passed. Current Phase 4 changes are not yet verified against PostgreSQL.

## Phase 4 — Quotations, sales orders, challans, and sales returns

- Implement quotation -> sales order -> delivery challan -> invoice conversion, partial delivery, and sales returns.
- Add sales reports from real data. Challans do not change stock unless separately confirmed.
- Preserve audit history and validate transitions end-to-end.

**Status:** Quotations, conversion to sales orders, order confirmation, partial delivery challans, delivered-quantity invoice drafts, sales reports, and quotation/order PDFs are implemented. Challans do not post stock movements. Sales returns are not implemented. The Phase 4 PostgreSQL integration test was skipped in the current checkout because `BEREV_TEST_DATABASE_URL` is not configured; browser acceptance also remains outstanding.

## Phase 5 — Purchasing and real costing

- Implement suppliers, purchase requests/orders, goods receipts, purchase invoices, supplier payments, and returns.
- Increase stock only on posted goods receipts. Replace purchase-price COGS snapshots with the confirmed costing method and document its effect on existing records.
- Add purchase/input-tax registers, valuation reports, and concurrency/valuation tests.

**Status:** The costing foundation and backend purchasing chain are implemented in code: warehouse-level moving average, immutable opening valuation, supplier/request/order workflows, partial GRNs, serial registration, GRNI/AP/input-GST postings, supplier payments, purchase returns, supplier ledger, purchase register, input-GST summary, and stock valuation report. Backend and PostgreSQL-gated tests have been added. **UNVERIFIED** on PostgreSQL; purchase UI, report exports, reversal/cancellation flows, and manual browser acceptance remain outstanding.

## Phase 6 — Expenses, dealers, accounting, reports, and administration

- Implement expense approval/posting, dealer portal isolation, full ledgers and accounting reports, persisted notifications, and user/audit administration.
- Complete report exports and configurable operational settings without inventing company data.

**Status:** Partially implemented: expense submission, approval/rejection, receipt upload/download, accounting posting, and a frontend screen are present. Dealer access isolation, full accounting/reporting and exports, notification scheduler/SMTP, user/password-reset/audit administration, and complete settings screens remain unimplemented.

## Phase 7 — Hardening, deployment, and acceptance

- Perform the security, performance, dependency-audit, and data-isolation work; complete deployment, backup/restore, and operational documentation.
- Run critical Playwright flows against a throwaway stack and map every section 25 acceptance criterion to evidence.
- Record remaining external requirements; do not describe the app as production-ready.

**Status:** Not complete. Security/performance audits, production deployment and tested restore, Playwright acceptance, acceptance-evidence mapping, and tax-professional decisions remain outstanding.

## Operating constraints

- Keep business credentials and secrets out of source, Compose files, and documentation.
- Use a scratch database for schema validation, `berev_dev` for development schema work, and `berev_test` for tests.
- Do not install software or download images without user approval.
- Do not mark the PostgreSQL schema verified until the exact schema runs cleanly on a real PostgreSQL instance.
- Configure official company/tax details rather than inventing them; tax/legal behavior requires qualified professional review.
