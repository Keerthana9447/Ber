# Production readiness checklist (BEREV ERP)

Built from the repository's own `KNOWN_GAPS.md`, `CHANGES.md` and a read of the code on 2026-11-09.
`[x]` = evidence exists in the repo (named below). `[ ]` = open. **Nothing here claims the system is production-ready.**
Owner: **Agent** = code the IDE agent can write and test; **You** = needs a person, an account, a server or a decision.

## Follow-up hardening snapshot (2026-11-09)

- Added production-mode API docs lockdown, explicit CORS allow-list validation, and API security headers; added tests. These changes have not been deployed to the running local API.
- Fixed the reviewed dealer access-control issue: dealer accounts can use the scoped dealer catalog, not the internal product/serial endpoints.
- Moved `pytest` to `backend/requirements-dev.txt`; added a PostgreSQL-backed CI workflow, dependency audits, pagination coverage, a mocked Playwright dealer sign-in, and a React text-escaping test.
- Local verification: 83 backend tests passed and 22 database-dependent tests were skipped because no test-only database URL was configured; 28 frontend tests and one Playwright test passed; `npm audit` reported zero vulnerabilities. The Python dependency audit is still pending.
- A read-only scan of the two available commits found non-empty username/password fields in `DATABASE_URL` and `TEST_DATABASE_URL` in the initial commit `38602e0`. Values are intentionally not reproduced here. Treat them as exposed: rotate the database credentials and authorize a history purge before sharing/pushing further commits. The branch tip is sanitized; Git history is not.
- The production-like validator against the already-running local API reported 16 passes, 8 warnings and 0 failures. It used no staging credentials or database checks and exercised the currently running app, not these un-deployed changes.
- Deployment, Traefik per-IP rate limits/security headers, database/receipt backup scripts, and an isolated restore-drill profile are now present. They have not been run because Docker is unavailable in this environment; encrypted off-host backup replication and a real restore drill remain release gates.
- The local browser smoke test only navigated 12 modules and observed no UI alerts; it is not accounting acceptance.

## 0. Release gate: all of these must be true before real invoices are issued
- [ ] Every section below is `[x]`, or the exception is written down and accepted by the owner of the business.
- [ ] `python scripts/staging_validate.py --base-url <staging> --db-checks --production-like` ends with **0 failed, 0 warnings** on a staging copy that mirrors production.
- [ ] A parallel run of at least a few weeks, comparing every invoice against the current billing method, shows no differences.
- [ ] Written sign-off from a qualified Indian tax professional (section 5).

## 1. Code and tests
- [x] Strict backend suite on PostgreSQL: 75 passed, 0 skipped, 0 errors (`KNOWN_GAPS.md`, 2026-11-08). *Re-run after every change; this patch adds tests that have not yet been run on PostgreSQL.*
- [x] Migrations build from an empty database, match the ORM models, and downgrade/upgrade (`tests/test_migrations.py`).
- [x] Database test config refuses anything except `berev_test` (`tests/test_database_test_config.py`); `REQUIRE_DB_TESTS=1` turns skips into failures.
- [x] Frontend: 27 tests, type-check and production build pass (reported 2026-11-09).
- [ ] Run this patch's new tests on PostgreSQL: `tests/test_preflight.py`, `tests/test_resilience.py`, `tests/test_staging_validate.py`. **Agent/You**
- [ ] Full Playwright end-to-end tests for the 15 scenarios in `BEREV_MASTER_PROMPT.md` section 21. **Agent** (one mocked dealer-login smoke test exists)
- [x] CI workflow for strict PostgreSQL backend, frontend tests/build, browser smoke tests and dependency audits (`.github/workflows/ci.yml`). **Not yet run on GitHub**

## 2. Security (nothing here has a test campaign yet)
- [x] Argon2 password hashing, JWT with refresh-token rotation and revocation, per-account lockout (PostgreSQL-tested).
- [x] Dealer isolation test (one dealer cannot read another's records by URL) passes on PostgreSQL.
- [ ] **Manual** two-dealer browser check of isolation. **You**
- [x] Request/IP rate limiting is configured in Traefik (120 API requests/minute, burst 20; 10 login requests/minute, burst 3). **Not runtime-validated; assumes direct client access to Traefik**
- [x] CORS origin allow-list and validation (`CORS_ALLOWED_ORIGINS`); no wildcard credentials.
- [x] API security headers and production-only HSTS; reverse-proxy CSP/routing configuration remains incomplete.
- [x] `/docs`, `/redoc` and `/openapi.json` are disabled when `ENVIRONMENT=production`.
- [ ] Log redaction review and a request-id on every log line (the formatter supports `request_id`; nothing sets it). **Agent**
- [ ] `pip-audit` and `npm audit` run and each finding fixed or accepted in writing. **Agent** (`npm audit` has 0 findings; Python audit pending)
- [ ] SQL-injection and XSS tests on search and free-text fields; upload tests beyond size/signature (e.g. polyglot files). **Agent** (tests added; database-backed injection test not run)
- [ ] Production secrets (`JWT_SECRET_KEY`, DB password, webhook secret, SMTP) generated fresh, stored outside the repo, with a rotation procedure. **You**
- [x] Scan available Git history for secrets. **Found exposed database credentials in the initial `.env.example`; rotate them and authorize history rewriting before further sharing.**

## 3. Data integrity and accounting
- [x] Invoice numbering concurrency, gapless series, rollback, stock never negative, journal balance and reversals (PostgreSQL-tested).
- [x] `python -m app.cli verify-integrity`: 8 checks pass on `berev_test`.
- [ ] Accounting review of the costing/GRNI/price-variance policy and chart of accounts by a qualified accountant (`KNOWN_GAPS.md`). **You**
- [ ] Customer opening-balance sign semantics decided and implemented (currently not included in statements). **You decide, Agent builds**
- [ ] Opening stock valuation entered from real purchase bills, reconciled to physical stock count. **You**
- [ ] Remaining reports and exports (aging, purchase exports), purchase cancellation/return reversal, dealer order creation, sales returns. **Agent** (see `KNOWN_GAPS.md` "Still not implemented")

## 4. Operations
- [ ] Complete and validate backend/frontend Docker deployment, HTTPS reverse proxy and per-IP rate limits. **Configuration and CI build check added; Docker runtime, certificate issuance and rate-limit behavior not validated locally**
- [ ] Production database: managed PostgreSQL or a hardened server, private network, least-privilege role (`berev_app`, not superuser). **You**
- [ ] Domain name and TLS certificate with auto-renewal. **You**
- [ ] Migration-on-deploy procedure and a tested rollback (restore from backup; most downgrades are not data-safe). **Agent writes, You rehearse**
- [ ] Automated `pg_dump` and receipt-volume backups, off-machine copy, retention policy. **Local backup loop added; encrypted off-host copy remains unimplemented**
- [ ] **Restore drill** into an isolated scratch container, then run `preflight` and `verify-integrity`. **Script and isolated Compose profile added; not run because Docker is unavailable**
- [x] Uploaded receipts (`EXPENSE_UPLOAD_DIR`) use a persistent volume and are included in local backup archives. **Off-host copy and restore execution remain open**
- [ ] Monitoring and alerts: `/api/v1/health/ready` (added in this patch) polled every minute, disk space, backup success, error-rate, `notification_scheduler_failed` log lines. **You**
- [ ] First-admin creation procedure on the production host (`python -m app.cli create-admin`), no default credentials, documented. **You**
- [ ] Load and failover test with realistic data volume; slow-query review with `EXPLAIN`. **Agent**

## 5. Business and legal (cannot be done by software)
- [ ] Tax professional confirms: current GST rates per HSN/SAC, mandatory invoice fields, invoice numbering (`T`/`B` suffix scheme), the invoice-date rule, cancellation rules, and **whether e-invoicing (IRN/QR) applies to BEREV**. If it does, an e-invoice phase must be built first. **You**
- [ ] BEREV's real GSTIN, PAN, bank and UPI details entered through Settings (never invented in code). **You**
- [ ] Privacy/legal review of stored customer data and retention. **You**

## 6. Acceptance by people
- [ ] Manual walkthrough in the browser of: settings, customers, products and stock, invoice issue/PDF/cancel, receipts, credit notes, purchases/GRN, expenses, dealer portal (two dealers), reports. **You**
- [ ] User manual per role and a short training session for staff. **Agent drafts, You review**

## What the automated staging check does and does not prove
`scripts/staging_validate.py` checks a running API from the outside: liveness, readiness (database reachable and migrated), unauthenticated access refused, bad-credential and malformed-input handling, token tampering, refresh rotation/replay/revocation, security-header and docs-exposure observations, a light parallel-request smoke test, and (with `--db-checks`) `preflight` and `verify-integrity`. It does **not** prove business correctness, tax correctness, load capacity, backup/restore, or that every endpoint enforces permissions.
