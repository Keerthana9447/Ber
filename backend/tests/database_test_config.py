from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.config import PROJECT_ROOT


def database_tests_required(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get("REQUIRE_DB_TESTS", "").strip().lower() in {"1", "true", "yes"}


def configured_test_database_url(
    environ: Mapping[str, str] | None = None,
    env_file: Path = PROJECT_ROOT / ".env",
) -> str | None:
    values = os.environ if environ is None else environ
    url = values.get("BEREV_TEST_DATABASE_URL") or values.get("TEST_DATABASE_URL")
    if not url:
        url = dotenv_values(env_file).get("TEST_DATABASE_URL")
    if not url:
        return None

    try:
        parsed = make_url(url)
    except ArgumentError:
        raise RuntimeError("The PostgreSQL test database URL is invalid.") from None
    if parsed.drivername != "postgresql+psycopg" or parsed.database != "berev_test":
        raise RuntimeError("PostgreSQL integration tests may connect only to berev_test.")
    return url


def require_test_database_url(
    environ: Mapping[str, str] | None = None,
    env_file: Path = PROJECT_ROOT / ".env",
) -> str:
    url = configured_test_database_url(environ, env_file)
    if url is None:
        raise RuntimeError(
            "PostgreSQL tests are required but no BEREV_TEST_DATABASE_URL or "
            "TEST_DATABASE_URL is configured."
        )
    return url
