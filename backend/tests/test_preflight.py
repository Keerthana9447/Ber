import asyncio
import logging
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.preflight import (
    DB_UNREACHABLE, SCHEMA_MISMATCH, SCHEMA_NOT_MIGRATED, evaluate_revisions, expected_heads, run_preflight,
)
from tests.test_config_and_api import make_settings

UNREACHABLE = "postgresql+psycopg://berev_app@127.0.0.1:1/berev_test"


def sqlite_engine(revision: str | None):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    if revision is not None:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    return engine


class EvaluateRevisionTests(unittest.TestCase):
    def test_matching_revision_is_ok(self):
        self.assertEqual(evaluate_revisions(["0010"], ["0010"]), [])

    def test_empty_database_is_not_migrated(self):
        self.assertEqual([p.code for p in evaluate_revisions([], ["0010"])], [SCHEMA_NOT_MIGRATED])

    def test_old_or_newer_schema_is_a_mismatch(self):
        self.assertEqual([p.code for p in evaluate_revisions(["0009"], ["0010"])], [SCHEMA_MISMATCH])
        self.assertEqual([p.code for p in evaluate_revisions(["0011"], ["0010"])], [SCHEMA_MISMATCH])


class RunPreflightTests(unittest.TestCase):
    def test_ok_when_database_is_at_head(self):
        result = run_preflight(sqlite_engine("0010"), heads=["0010"])
        self.assertTrue(result.ok)
        self.assertEqual(result.current_revisions, ("0010",))

    def test_reports_missing_and_stale_schema(self):
        self.assertEqual(run_preflight(sqlite_engine(None), heads=["0010"]).codes, [SCHEMA_NOT_MIGRATED])
        self.assertEqual(run_preflight(sqlite_engine("0008"), heads=["0010"]).codes, [SCHEMA_MISMATCH])

    def test_unreachable_database_is_reported_without_raising(self):
        engine = create_engine(UNREACHABLE, connect_args={"connect_timeout": 2})
        try:
            with self.assertLogs("berev.preflight", level=logging.ERROR):
                result = run_preflight(engine, heads=["0010"])
        finally:
            engine.dispose()
        self.assertEqual(result.codes, [DB_UNREACHABLE])

    def test_expected_head_is_read_from_the_real_migration_scripts(self):
        heads = expected_heads()
        self.assertEqual(len(heads), 1, "the migration history must have exactly one head")


class ReadinessAndStartupTests(unittest.TestCase):
    def test_ready_endpoint_returns_503_with_codes_only_when_database_is_down(self):
        app = create_app(make_settings(database_url=UNREACHABLE))

        async def call():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                return await client.get("/api/v1/health/ready")

        try:
            with self.assertLogs("berev.preflight", level=logging.ERROR):
                response = asyncio.run(call())
        finally:
            app.state.engine.dispose()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["problems"], [DB_UNREACHABLE])
        self.assertNotIn("127.0.0.1", response.text)

    def test_strict_startup_refuses_to_start_on_a_bad_database(self):
        app = create_app(make_settings(database_url=UNREACHABLE, startup_preflight="strict"))

        async def start():
            async with app.router.lifespan_context(app):
                pass

        with self.assertLogs("berev.preflight", level=logging.ERROR):
            with self.assertRaises(RuntimeError):
                asyncio.run(start())
        app.state.engine.dispose()

    def test_preflight_defaults_by_environment(self):
        self.assertEqual(make_settings(environment="production").preflight_mode, "strict")
        self.assertEqual(make_settings(environment="development").preflight_mode, "warn")
        self.assertEqual(make_settings(environment="test").preflight_mode, "off")
        self.assertEqual(make_settings(environment="production", startup_preflight="warn").preflight_mode, "warn")

    def test_scheduler_failure_logs_type_and_traceback_then_recovers(self):
        calls = {"n": 0}

        def flaky(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("simulated outage")
            return 0

        app = create_app(make_settings(startup_preflight="off", notification_interval_seconds=10))

        async def run():
            with patch("app.main.process_notification_batch", flaky), \
                 patch("app.main.retry_delay_seconds", lambda *_: 0.05):
                async with app.router.lifespan_context(app):
                    await asyncio.sleep(0.4)

        with self.assertLogs("berev.api", level=logging.INFO) as captured:
            asyncio.run(run())
        app.state.engine.dispose()
        joined = "\n".join(captured.output)
        self.assertIn("notification_scheduler_failed: ConnectionError", joined)
        self.assertIn("consecutive failures: 1", joined)
        self.assertIn("Traceback", joined)
        self.assertIn("notification_scheduler_recovered", joined)


if __name__ == "__main__":
    unittest.main()
