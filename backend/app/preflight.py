"""Startup / readiness checks: can we reach the database, and is its schema at the migration
head this application version expects? Detail goes to logs; callers that face the public
should expose only the short codes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from sqlalchemy import Engine, text

logger = logging.getLogger("berev.preflight")
BACKEND_ROOT = Path(__file__).resolve().parents[1]

DB_UNREACHABLE = "database_unreachable"
SCHEMA_NOT_MIGRATED = "schema_not_migrated"
SCHEMA_MISMATCH = "schema_revision_mismatch"


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


@dataclass(frozen=True)
class PreflightResult:
    problems: tuple[Problem, ...]
    current_revisions: tuple[str, ...] = ()
    expected_heads: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def codes(self) -> list[str]:
        return [p.code for p in self.problems]


def evaluate_revisions(current: Sequence[str], heads: Sequence[str]) -> list[Problem]:
    """Pure comparison of the database revision(s) against the application's migration head(s)."""
    if not current:
        return [Problem(SCHEMA_NOT_MIGRATED,
                        "The database has no migration version. Run: alembic upgrade head")]
    if set(current) != set(heads):
        return [Problem(
            SCHEMA_MISMATCH,
            f"Database schema is at {', '.join(sorted(current))} but this application expects "
            f"{', '.join(sorted(heads))}. Run `alembic upgrade head` (or deploy the matching app version).",
        )]
    return []


@lru_cache
def expected_heads() -> tuple[str, ...]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


def run_preflight(engine: Engine, heads: Sequence[str] | None = None) -> PreflightResult:
    from alembic.runtime.migration import MigrationContext

    wanted = tuple(heads) if heads is not None else expected_heads()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = tuple(MigrationContext.configure(connection).get_current_heads())
    except Exception as error:  # any driver/SQLAlchemy failure means "not ready"
        logger.error("preflight_database_check_failed: %s", type(error).__name__, exc_info=True)
        return PreflightResult(
            (Problem(DB_UNREACHABLE, f"Cannot connect to the database ({type(error).__name__}). "
                                     "Check that PostgreSQL is running and DATABASE_URL is correct."),),
            expected_heads=wanted,
        )
    return PreflightResult(tuple(evaluate_revisions(current, wanted)), current, wanted)
