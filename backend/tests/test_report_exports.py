from __future__ import annotations

import asyncio
import csv
import io
import unittest
import uuid
from datetime import date
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import select, text

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import AuditLog, Branch, JournalEntry, JournalEntryLine, Permission, Role, State, User
from app.seed import seed_reference_data
from tests.database_test_config import (
    configured_test_database_url,
    database_tests_required,
    require_test_database_url,
)


@unittest.skipUnless(
    configured_test_database_url() is not None or database_tests_required(),
    "Set TEST_DATABASE_URL or BEREV_TEST_DATABASE_URL to run PostgreSQL report tests",
)
class ReportExportPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(
            _env_file=PROJECT_ROOT / ".env",
            database_url=require_test_database_url(),
        )
        cls.app = create_app(cls.settings)
        with cls.app.state.engine.connect():
            pass
        cls.factory = cls.app.state.session_factory
        cls.email = f"report-export-{uuid.uuid4().hex}@example.test"
        cls.reference = f"PH6-{uuid.uuid4().hex[:16]}"
        with cls.factory.begin() as session:
            seed_reference_data(session)
            role = session.scalar(select(Role).where(Role.name == "Super Admin"))
            if role is None:
                raise RuntimeError("The reference seed did not create Super Admin.")
            user = User(
                email=cls.email,
                full_name="Report Export Integration Test",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=role.id,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
            cls.user_id = user.id
            entry = JournalEntry(
                entry_date=date(2026, 11, 8),
                reference=cls.reference,
                narration="Report export regression",
                source_type="test",
                source_id=user.id,
                posted_by=user.id,
            )
            session.add(entry)
            session.flush()
            cls.entry_id = entry.id
            session.add_all([
                JournalEntryLine(entry_id=entry.id, account_code="1000", debit=Decimal("125.00"), credit=0),
                JournalEntryLine(entry_id=entry.id, account_code="4000", debit=0, credit=Decimal("125.00")),
            ])
        cls.token = create_token_pair(cls.settings, cls.user_id).access_token

    @staticmethod
    def _reset_append_only_entries(session, entry_ids: set[int] | list[int] | int):
        if isinstance(entry_ids, int):
            entry_ids = {entry_ids}
        else:
            entry_ids = set(entry_ids)
        if not entry_ids:
            return
        session.execute(text("ALTER TABLE journal_entry_lines DISABLE TRIGGER jel_append_only"))
        session.execute(text("ALTER TABLE journal_entries DISABLE TRIGGER je_append_only"))
        session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_append_only"))
        try:
            session.query(JournalEntryLine).filter(JournalEntryLine.entry_id.in_(sorted(entry_ids))).delete(synchronize_session=False)
            session.query(JournalEntry).filter(JournalEntry.id.in_(sorted(entry_ids))).delete(synchronize_session=False)
        finally:
            session.execute(text("ALTER TABLE journal_entry_lines ENABLE TRIGGER jel_append_only"))
            session.execute(text("ALTER TABLE journal_entries ENABLE TRIGGER je_append_only"))
            session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_append_only"))

    @classmethod
    def tearDownClass(cls):
        with cls.factory.begin() as session:
            cls._reset_append_only_entries(session, {cls.entry_id})
            session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_append_only"))
            try:
                session.query(AuditLog).filter(AuditLog.user_id == cls.user_id).delete(synchronize_session=False)
                session.query(User).filter(User.id == cls.user_id).delete()
            finally:
                session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_append_only"))
        cls.app.state.engine.dispose()

    def _request(self, method, path, **kwargs):
        async def run():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {self.token}"},
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run())

    def test_trial_balance_and_exports_share_totals_and_audit(self):
        query = "?from=2026-11-08&to=2026-11-08"
        api_response = self._request("GET", f"/api/v1/accounting/trial-balance{query}")
        self.assertEqual(api_response.status_code, 200, api_response.text)
        api = api_response.json()
        self.assertEqual(api["total_debit"], "125.00")
        self.assertEqual(api["total_credit"], "125.00")
        self.assertTrue(api["is_balanced"])

        csv_response = self._request("GET", f"/api/v1/reports/export/trial-balance.csv{query}")
        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        parsed = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
        summary = {row[0]: row[1] for row in parsed if len(row) == 2}
        self.assertEqual(summary["total_debit"], api["total_debit"])
        self.assertEqual(summary["total_credit"], api["total_credit"])

        xlsx_response = self._request("GET", f"/api/v1/reports/export/trial-balance.xlsx{query}")
        self.assertEqual(xlsx_response.status_code, 200, xlsx_response.text)
        workbook = load_workbook(io.BytesIO(xlsx_response.content), data_only=True)
        values = {row[0]: row[1] for row in workbook["Summary"].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(Decimal(str(values["total_debit"])), Decimal(api["total_debit"]))
        self.assertEqual(Decimal(str(values["total_credit"])), Decimal(api["total_credit"]))

        pdf_response = self._request("GET", f"/api/v1/reports/export/trial-balance.pdf{query}")
        self.assertEqual(pdf_response.status_code, 200, pdf_response.text)
        self.assertEqual(pdf_response.content[:5], b"%PDF-")
        self.assertIn("125.00", PdfReader(io.BytesIO(pdf_response.content)).pages[0].extract_text())

        with self.factory() as session:
            audit = session.scalar(
                select(JournalEntry).where(JournalEntry.reference == self.reference)
            )
            self.assertIsNotNone(audit)
            from app.models import AuditLog

            logs = session.query(AuditLog).filter(
                AuditLog.user_id == self.user_id,
                AuditLog.entity_type == "report",
                AuditLog.action == "export_report",
            ).count()
            self.assertGreaterEqual(logs, 3)

    def test_all_phase_six_report_keys_support_csv_xlsx_and_pdf(self):
        date_query = "&from=2026-11-08&to=2026-11-08"
        sales_query = "&from_date=2026-11-08&to_date=2026-11-08"
        keys = (
            ("trial-balance", date_query),
            ("profit-and-loss", date_query),
            ("cash-book", date_query),
            ("bank-book", date_query),
            ("sales", sales_query),
            ("purchase-register", date_query),
            ("input-gst", date_query),
            ("stock-valuation", ""),
            ("sales-register", date_query),
            ("gst-summary", date_query),
            ("ageing", "&party_type=customer&as_of=2026-11-08"),
        )
        for report_key, query in keys:
            for file_format, signature in (
                ("csv", b"\xef\xbb\xbf"),
                ("xlsx", b"PK"),
                ("pdf", b"%PDF-"),
            ):
                with self.subTest(report_key=report_key, file_format=file_format):
                    response = self._request(
                        "GET",
                        f"/api/v1/reports/export/{report_key}.{file_format}?x=1{query}",
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.content.startswith(signature))

    def test_exports_require_export_permission(self):
        with self.factory.begin() as session:
            read_permission = session.scalar(
                select(Permission).where(Permission.code == "reports:read")
            )
            self.assertIsNotNone(read_permission)
            read_only_role = Role(
                name=f"Report Reader {uuid.uuid4().hex}",
                permissions=[read_permission],
            )
            session.add(read_only_role)
            session.flush()
            restricted = User(
                email=f"report-read-only-{uuid.uuid4().hex}@example.test",
                full_name="Report Read Only",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=read_only_role.id,
                must_change_password=False,
            )
            session.add(restricted)
            session.flush()
            restricted_id = restricted.id
            restricted_role_id = read_only_role.id
        restricted_token = create_token_pair(self.settings, restricted_id).access_token

        async def run():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {restricted_token}"},
            ) as client:
                return await client.get("/api/v1/reports/export/trial-balance.csv")

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 403, response.text)
        with self.factory.begin() as session:
            session.query(User).filter(User.id == restricted_id).delete()
            session.query(Role).filter(Role.id == restricted_role_id).delete()

    def test_branch_manager_export_is_limited_to_assigned_branch(self):
        with self.factory.begin() as session:
            branch_state = session.scalar(select(State).order_by(State.code).limit(1))
            self.assertIsNotNone(branch_state)
            branch = Branch(
                code=f"RPT-{uuid.uuid4().hex[:12]}",
                name="Report Export Branch",
                state_code=branch_state.code,
            )
            other_branch = Branch(
                code=f"RPT-{uuid.uuid4().hex[:12]}",
                name="Other Report Branch",
                state_code=branch_state.code,
            )
            role = session.scalar(select(Role).where(Role.name == "Branch Manager"))
            self.assertIsNotNone(role)
            export_permission = session.scalar(
                select(Permission).where(Permission.code == "reports:export")
            )
            self.assertIsNotNone(export_permission)
            role_had_export = export_permission in role.permissions
            if not role_had_export:
                role.permissions.append(export_permission)
            session.add_all([branch, other_branch])
            session.flush()
            branch_ids = (branch.id, other_branch.id)
            user = User(
                email=f"branch-report-{uuid.uuid4().hex}@example.test",
                full_name="Branch Report Manager",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=role.id,
                branch_id=branch.id,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
            branch_user_id = user.id
            entries = []
            for entry_branch, amount in ((branch, Decimal("40.00")), (other_branch, Decimal("700.00"))):
                entry = JournalEntry(
                    entry_date=date(2026, 11, 8),
                    reference=f"PH6-{uuid.uuid4().hex[:16]}",
                    narration="Branch-scope report regression",
                    branch_id=entry_branch.id,
                    posted_by=user.id,
                )
                session.add(entry)
                session.flush()
                session.add_all([
                    JournalEntryLine(entry_id=entry.id, account_code="1000", debit=amount, credit=0),
                    JournalEntryLine(entry_id=entry.id, account_code="4000", debit=0, credit=amount),
                ])
                entries.append(entry.id)
        branch_token = create_token_pair(self.settings, branch_user_id).access_token

        async def run():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {branch_token}"},
            ) as client:
                return await client.get(
                    "/api/v1/reports/export/trial-balance.csv?from=2026-11-08&to=2026-11-08"
                )

        response = asyncio.run(run())
        self.assertEqual(response.status_code, 200, response.text)
        parsed = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        summary = {row[0]: row[1] for row in parsed if len(row) == 2}
        self.assertEqual(summary["total_debit"], "40.00")
        self.assertEqual(summary["total_credit"], "40.00")
        with self.factory.begin() as session:
            self._reset_append_only_entries(session, entries)
            session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_append_only"))
            try:
                session.query(AuditLog).filter(AuditLog.user_id == branch_user_id).delete(synchronize_session=False)
                session.query(User).filter(User.id == branch_user_id).delete()
            finally:
                session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_append_only"))
            session.query(Branch).filter(Branch.id.in_(branch_ids)).delete(synchronize_session=False)
            role = session.scalar(select(Role).where(Role.name == "Branch Manager"))
            if not role_had_export:
                role.permissions[:] = [
                    permission for permission in role.permissions if permission.code != "reports:export"
                ]
