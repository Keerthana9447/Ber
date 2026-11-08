from __future__ import annotations

import re
import unittest
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.models import Base
from tests.database_test_config import (
    configured_test_database_url,
    database_tests_required,
    require_test_database_url,
)


@unittest.skipUnless(
    configured_test_database_url() is not None or database_tests_required(),
    "Set TEST_DATABASE_URL or BEREV_TEST_DATABASE_URL to run PostgreSQL migration tests",
)
class MigrationSchemaTests(unittest.TestCase):
    def test_empty_database_migrates_to_head_without_schema_drift(self):
        engine = create_engine(require_test_database_url())
        schema_name = f"migration_test_{uuid.uuid4().hex}"
        try:
            with engine.connect() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                connection.commit()
                connection.execute(text(f'SET search_path TO "{schema_name}"'))
                connection.commit()

                backend_root = Path(__file__).resolve().parents[1]
                config = Config(str(backend_root / "alembic.ini"))
                config.set_main_option("script_location", str(backend_root / "alembic"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")

                migration_context = MigrationContext.configure(
                    connection,
                    opts={"compare_type": True, "compare_server_default": True},
                )
                differences = compare_metadata(migration_context, Base.metadata)
                self.assertEqual(differences, [], f"Alembic schema drift: {differences!r}")

                expected_triggers = set(
                    re.findall(
                        r"\bCREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+([a-z_][a-z0-9_]*)",
                        (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(
                            encoding="utf-8"
                        ),
                        re.IGNORECASE,
                    )
                )
                actual_triggers = set(
                    connection.execute(
                        text(
                            """
                            SELECT t.tgname
                            FROM pg_trigger AS t
                            JOIN pg_class AS c ON c.oid = t.tgrelid
                            JOIN pg_namespace AS n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema_name AND NOT t.tgisinternal
                              AND t.tgenabled = 'O'
                            """
                        ),
                        {"schema_name": schema_name},
                    ).scalars()
                )
                self.assertEqual(
                    actual_triggers,
                    expected_triggers,
                    "Installed triggers differ from the canonical schema.",
                )

                command.downgrade(config, "base")
                command.upgrade(config, "head")
                migration_context = MigrationContext.configure(
                    connection,
                    opts={"compare_type": True, "compare_server_default": True},
                )
                differences = compare_metadata(migration_context, Base.metadata)
                self.assertEqual(
                    differences,
                    [],
                    f"Alembic schema drift after downgrade/upgrade: {differences!r}",
                )
        finally:
            with engine.begin() as connection:
                connection.execute(text("SET search_path TO public"))
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            engine.dispose()
