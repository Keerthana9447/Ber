"""Apply the canonical PostgreSQL schema as the initial Alembic baseline.

Revision ID: 0001_schema_baseline
Revises:
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import op

revision = "0001_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
    script = schema_path.read_text(encoding="utf-8")
    script = re.sub(r"\A\s*BEGIN\s*;", "", script, count=1, flags=re.IGNORECASE)
    script = re.sub(r";\s*COMMIT\s*;\s*\Z", ";", script, count=1, flags=re.IGNORECASE)
    raw_connection = op.get_bind().connection.driver_connection
    with raw_connection.cursor() as cursor:
        cursor.execute(script, prepare=False)


def downgrade() -> None:
    raise RuntimeError(
        "The initial schema baseline is irreversible; restore a deliberate database backup instead."
    )
