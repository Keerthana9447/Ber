# Changes

## 2026-11-08 — PostgreSQL migration repair and strict acceptance verification

- Fixed the Alembic root cause: revision `0001_schema_baseline` had read the current `backend/db/schema.sql`, so it created columns and tables that later migrations tried to add again. It now applies a frozen DDL snapshot captured from the original baseline commit (`11d61a7`); revisions `0002`–`0009` apply their own changes in order, without schema-overlap `IF NOT EXISTS` workarounds.
- Corrected the later migration operations and downgrade paths. Downgrading `0005_payments_credit_notes` now refuses to discard populated snapshot data. Added a strict PostgreSQL test that upgrades a fresh schema, checks Alembic autogenerate against model metadata (tables, columns, nullability, constraints, indexes), checks installed triggers, and downgrades to base and upgrades again.
- The new comparison found `invoice_items_order_item_ix` was in the migration but missing from the canonical schema/model. Added the index to `backend/db/schema.sql` and regenerated the ORM mapping.
- Recreated only `berev_dev` and `berev_test`, owned by `berev_app` (`rolsuper=false`, `rolcreatedb=false`). Both migrated to head; `berev_test` completed downgrade to base and upgrade to head. Ran the reference seed on `berev_dev` only.
- Fixed supplier payment creation to allocate its immutable `SPAY-########` number before insert, using the transactional number sequence. Normalized the receipt storage setting to `Path` for both upload and download. Updated inventory-backed integration scenarios to establish opening valuations through the existing valuation workflow before selling stock; no tests were removed, skipped, or weakened.
- PostgreSQL 16.15 answered on `127.0.0.1:5432`. The earlier project notes describe prior tests using a local-profile PostgreSQL cluster launched by `pg_ctl`; they do not establish why that cluster stopped. Earlier-phase API coverage was reproduced on fresh databases on the currently responding service.
- Strict full backend suite against `berev_test`: **75 passed, 0 skipped, 0 errors**. `python -m app.cli verify-integrity` against `berev_test`: PASS for duplicate invoice numbers, numbering series gaps, inventory ledger parity, unbalanced journal entries, trial balance parity, issued invoice totals, issued invoice AR parity, and issued invoices with journal entries.
- **Still unverified:** manual browser acceptance (including two-dealer URL isolation and purchase receipt/stock/valuation walkthrough), production load/failover, deployment, backup/restore, and qualified GST/accounting review. Phase 7 was not started.

The dated Phase 5/6 entries below record implementation status before this PostgreSQL verification and are superseded where their validation status differs.

## 2026-11-08 — Phase 5 purchasing workflow implementation

- Added supplier list/create/update endpoints and purchase-request approval/order conversion, with audit events and duplicate-safe document identifiers.
- Added transactional, partial goods receipts. Stock movements, serial records, warehouse quantity, and weighted-average cost update together only when the GRN commits; order/inventory row locks protect concurrent receipts.
- Added GRNI journals at receipt and purchase-invoice posting with input GST, AP supplier-ledger attribution, and a purchase-price variance account for invoice/receipt cost differences.
- Added partial supplier invoices, supplier payments with explicit or oldest-first allocation, supplier ledgers, debit notes/purchase returns, purchase register, input-GST-by-rate summary, and stock valuation report.
- Added purchase chain/GRN/debit-note schema changes and migration `0008_purchasing_workflows`.
- Added hand-computed GRNI/purchase-price-variance accounting coverage and PostgreSQL-gated workflow coverage for concurrent GRNs, partial receipt/invoicing, duplicate serial rejection, average-cost valuation, and payables settlement.
- Updated payments list serialization to expose supplier party names and purchase allocations.
- Historical validation at implementation time: 65 backend tests passed and 9 PostgreSQL-gated tests were skipped. The later strict PostgreSQL validation is recorded above.
- **Still outstanding:** remaining Phase 5 reversals/exports/UI, all listed Phase 6 work, and Phase 7 hardening/deployment/acceptance. This is not a completion claim for Phases 5–7.

## 2026-11-08 — Phase 6 expense workflow foundation

- Added expense category reference rows for the configured expense chart accounts and API workflows for submission, approval/rejection, and one-time journal posting.
- Expense posting debits the selected expense account and separately debits input CGST/SGST/IGST/cess accounts, crediting the selected cash/bank ledger for the total. Posting is locked and transactional; posted expense status and journal are committed together.
- Added raw receipt upload/download endpoints with a 5 MB cap, PDF/JPEG/PNG/WebP media and signature validation, generated filenames, and storage outside the web root using `EXPENSE_UPLOAD_DIR`.
- Added the React expense screen for recording, receipt upload/download, approvals, and posting; expanded the API contract and added a `0009_expense_workflow` migration.
- Added journal-derived journal-entry list, trial balance, P&L (explicitly before income tax), and cash/bank book endpoints; the Accounting navigation now lists posted journal entries. A PostgreSQL integration assertion checks balanced trial-balance report totals.
- Validation: 67 backend tests pass, 10 PostgreSQL-gated tests are skipped; 21 frontend tests pass; TypeScript check/production build pass; ORM generation check and OpenAPI route registration pass. PostgreSQL receipt/expense integration tests remain unexecuted because no test URL is configured.
- **Still outstanding:** the remaining Phase 5 interfaces/reversals/exports; dealer isolation and user/admin/report/notification/settings requirements; and all Phase 7 hardening, audits, deployment, backup/restore, Playwright acceptance, and external sign-offs.

## 2026-11-08 — Phase 6 dealer portal foundation

- Added dealer profile and dealer-priced catalog APIs and a portal page; dealer accounts land on the portal instead of the admin dashboard.
- Applied a dealer/customer SQL scope to customer-linked sales documents, invoices, payments, credit notes, and customer ledgers. Added a PostgreSQL-gated test for Dealer A attempting to list or guess Dealer B records.
- Documented the portal endpoints and record isolation contract.
- Validation: 57 backend tests pass and 11 PostgreSQL-gated tests are skipped; 21 frontend tests pass and the TypeScript/production build passes; API startup exposes both dealer endpoints and Alembic reports `0009_expense_workflow` as head. The cross-dealer isolation integration test is among those not run because no test database URL is configured.
- **Still outstanding:** dealer order creation and database verification of the isolation behavior, along with remaining Phase 5 interfaces/reversals/exports; other Phase 6 accounting, reports/exports, notifications, user administration and settings; and Phase 7 hardening, audits, deployment, backup/restore, Playwright acceptance, and external sign-offs.

## 2026-11-08 — PostgreSQL test gate and security-test coverage

- Fixed test URL resolution to read `TEST_DATABASE_URL` from the root `.env` or `BEREV_TEST_DATABASE_URL` from the environment, with an explicit `berev_test` database-name guard and no fallback to the development `DATABASE_URL`.
- Added `REQUIRE_DB_TESTS=1` behavior and documented the strict CI command. Added database-gated refresh rotation/logout-revocation and login-lockout tests, plus oversized receipt coverage.
- Verification: 52 database-independent backend tests passed. The strict full suite reported 61 passed, 13 setup errors, and 0 skipped because the PostgreSQL service is absent and connection timed out. Integrity verification, explicitly targeted at `berev_test`, also could not connect; no integrity checks ran.
- **Not completed:** PostgreSQL behavior and the new database-gated API tests remain unverified. No security review is claimed; remaining security test gaps are listed in `KNOWN_GAPS.md`.

## 2026-11-08 — Phase 5 weighted-average costing foundation

- Added warehouse-level `inventory.average_unit_cost`, immutable opening-valuation rows, and migration `0007_weighted_average_cost` (after the current Phase 4 migration).
- Added hand-computed weighted-average helpers/tests for a first receipt, differently priced second receipt, a sale between receipts, and an opening valuation.
- Added admin-only opening valuation API and Stock-screen batch form. Each batch posts one balanced Inventory (1200) / Owner's Equity (3000) journal entry and an audit event; valuation records and journal entries are not edited.
- Added admin-authenticated `python -m app.cli set-opening-valuation` for command-line batch entry. Opening valuations can be reversed only before subsequent stock movements; the reversal is posted as a separate journal entry.
- Changed invoice issuance to snapshot warehouse average cost for COGS and return 409 with product names when positive on-hand stock has no opening valuation. It no longer falls back to `purchase_price` or zero.
- Existing stock is intentionally migrated with unknown (`NULL`) cost. No historical invoice or journal data is rewritten; admins must enter verified opening costs, which increase Inventory and Owner's Equity equally.
- Validation and PostgreSQL integration are pending. The app/test database is not configured in this checkout; tests must run only against the isolated PostgreSQL test database.

## 2026-11-08 — Phase 4 sales documents continuation

- Added the quotation -> sales order -> delivery challan -> delivered-quantity invoice-draft workflow, sales reports, and quotation/order PDFs with audit events. Challans are delivery records and do not post stock movements.
- Added canonical schema and generated model updates plus migration `0006_sales_document_chain`.
- Preserved quotation notes and discounts when editing an existing quotation in the frontend.
- Validation in this checkout: generated ORM mappings match `schema.sql`; frontend TypeScript and production build pass. The targeted PostgreSQL Phase 4 integration test was skipped because `BEREV_TEST_DATABASE_URL` is not configured.
- **Still outstanding:** PostgreSQL migration/API integration, manual browser acceptance, sales returns, purchases, expenses, and production operational validation.

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

## 2026-11-07 — Phase 2b: company settings, customers, and invoice drafts

- Added permission-checked company settings GET/PUT and readiness endpoints. GSTIN checksum/state, PAN, invoice prefix, state, and financial-year start month are validated. Audit records omit bank account, IFSC, and UPI values.
- Added customer state lookup, search/pagination/filtering, GSTIN/PAN/phone checks, duplicate active GSTIN conflict responses, and soft deletion.
- Added draft-only invoice create/read/update/delete and preview APIs. Preview and persisted amounts, tax splits, HSN summaries, round-off, and words are computed by `berev_core`; active database GST rates and company state are used. Drafts remain unnumbered and do not change stock.
- Added validation for active customers/products/HSN codes, available serialized products, duplicate serials, quantity precision, and active GST rates. Verified the PostgreSQL invoice guard triggers reject edits to issued invoices/items.
- Added the settings, customer, and invoice editor screens; readiness gating; debounced server-only totals; and lazy chart loading with Recharts split into chunks below the Vite large-chunk threshold.
- Validation on local PostgreSQL 16.15 test database: complete backend suite passed (55 tests). Frontend suite passed (20 tests), TypeScript typecheck passed, and production build succeeded without a large-chunk warning. Vite still reports dependency annotation notices from Zod that it removes safely.
- Not implemented in this phase: invoice issuance/numbering, PDFs, payments, stock deduction/reservation, quotations, or production deployment. A browser walkthrough and tax-professional review are still required before live invoicing.

## 2026-11-07 — Phase 3a: invoice issue, cancel, and PDF

- Added warehouse selection to stocked invoice lines and persisted each line's warehouse through the `0002` migration. Serialized lines use the serial's current warehouse; issue and cancellation apply movements to the stored warehouse.
- Added approval, issue, cancellation, and proforma conversion APIs with role permissions, audit events, idempotency-key handling, locked transactional numbering, stock/serial checks, stock movements, issue-time cost snapshots, balanced journal postings, and accounting reversals.
- Issued and cancelled invoices are protected by database triggers. Proformas remain draft-only, do not consume numbers or stock, and can be copied into a new tax-invoice draft.
- Added authenticated ReportLab PDFs that render stored invoice values/snapshots and omit unconfigured company/bank blocks. Tax invoices and bills of supply append `T` and `B` to the configured prefix to avoid display-number collisions across their separate series; confirm this numbering convention with a tax professional.
- Added `python -m app.cli verify-integrity` for duplicate numbers, numbering gaps, inventory-ledger parity, journal balance, trial-balance parity, invoice totals/AR parity, and missing journal entries.
- Added migrations for workflow fields and guards. Migrations through `0004_invoice_cancel_update_timestamp` were applied to the isolated local PostgreSQL test database.
- PostgreSQL integration test names and audited coverage:
  - `test_settings_customers_invoice_drafts_and_postgres_guards`: 20 parallel issues produce unique contiguous numbers; five parallel issues for one draft yield one success; two invoices contesting the last unit yield one success; injected failures roll back number, status, stock, serial, movements, and journals; cancellation reverses journals/restores stock; direct SQL trigger rejection; lifecycle 401/403; PDF content, multi-page layout, and omitted empty bank details.
  - `test_phase3a_accounting_balances_across_issued_invoices`: checks hand-computed AR, sales, CGST, SGST, IGST, COGS, inventory, and balanced journal totals across intra-state and inter-state invoices.
- Validation: backend suite **56 passed** on PostgreSQL; frontend suite **21 passed**; TypeScript check and production build succeeded. `verify-integrity` passed all eight checks after legacy test-fixture cleanup. The added accounting setup exposed an integration test's implicit first-page assumption; its customer query now requests a sufficient page size.
- **Still required before live use:** manual browser acceptance; qualified tax-professional confirmation of GST assumptions, invoice numbering markers and the conservative date rule; proper weighted-average/FIFO costing when purchases are implemented; and production security, deployment, backup/restore, and load/failover validation. IRN cancellation windows and GST return implications are not handled.

## 2026-11-07 — Phase 3b: payments and credit notes

- Added migration `0005_payments_credit_notes` and canonical schema changes for credit-note snapshots, payment/allocation immutability, allocation constraints, and seller-state snapshots.
- Added authenticated APIs for customer receipts and advances, explicit/FIFO allocation, receipt reversal, customer refunds from available advances or selected invoice credit, signed gateway webhooks, invoice payment balances, credit notes with optional restock, customer ledgers, and stored-value receipt/credit-note PDFs.
- Refunds and reversals retain original payment history. Credit note and refund transactions post through the accounting domain functions; invoice totals, credited amounts, allocated receipts, and refunds determine balance output from source records.
- Added Payments, Credit Notes, and Customer Ledger screens with loading/error/empty states, confirmation for refunds, reversals, and credit-note issuance, paginated histories/statements, invoice receipt balances, and server-side validation errors.
- Updated the payment ledger so reversals appear as offsetting events and a paged statement reports a stable overall closing balance.
- PostgreSQL test coverage includes `test_phase3b_payments_credit_notes_webhook_and_ledger` (partial/full receipts, concurrent over-allocation, reversals, credit-note tax parity/restock, PDF, signed webhook, ledger paging, capped invoice-credit/advance refunds, refund reversal, and authorization) and `test_customer_refunds_debit_the_selected_credit_account`.
- Validation on the isolated PostgreSQL test database: **58 backend tests passed**; frontend **21 tests passed**, TypeScript typecheck passed, production build succeeded, generated ORM mappings are in sync, and `python -m app.cli verify-integrity` passed all eight checks. The build emits only existing Zod dependency annotation notices.
- Gateway processing remains disabled unless `GATEWAY_WEBHOOK_SECRET` is configured; no external payment provider was connected.
- **Known limitation:** the customer master `opening_balance` field has no debit/credit direction; customer statements currently derive opening balances from earlier recorded transactions and do not incorporate that unsigned master field.
- **Still required before live use:** browser walkthrough of payment, refund, credit-note, invoice-balance and statement flows; tax-professional sign-off; IRN applicability/cancellation guidance; proper weighted-average/FIFO costing; and production security, deployment, backup/restore, and load/failover validation.


## 2026-11-09 - startup preflight, readiness endpoint, scheduler logging, staging validator (UNVERIFIED on PostgreSQL)
- Added `app/preflight.py` (database reachability + Alembic head check), `STARTUP_PREFLIGHT` setting, `python -m app.cli preflight`, and `GET /api/v1/health/ready`.
- Scheduler in `app/main.py` now logs error type, consecutive failures, retry delay and traceback (first failure, then every 10th), backs off exponentially (cap 900 s) and logs recovery. `JsonFormatter` now emits the structured extras.
- Added `scripts/staging_validate.py` and tests `test_resilience.py`, `test_preflight.py`, `test_staging_validate.py`; added `docs/PRODUCTION_READINESS.md`.
- Verification actually performed when this change was written (no PostgreSQL, SQLAlchemy or pytest in that environment): the new modules compile; `tests/test_resilience.py` (5) and `tests/test_staging_validate.py` (3, against stub servers) pass; the revision-comparison logic was exercised with a stubbed import. `tests/test_preflight.py` and the full strict suite have NOT been run.
