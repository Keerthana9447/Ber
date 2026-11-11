"""Apply the canonical PostgreSQL schema as the initial Alembic baseline.

Revision ID: 0001_schema_baseline
Revises:
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema_path = Path(__file__).with_name("0001_schema_baseline.sql")
    script = schema_path.read_text(encoding="utf-8")
    raw_connection = op.get_bind().connection.driver_connection
    with raw_connection.cursor() as cursor:
        cursor.execute(script, prepare=False)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          object_record RECORD;
          schema_name TEXT := current_schema();
        BEGIN
          FOR object_record IN
            SELECT tablename FROM pg_tables
             WHERE schemaname = schema_name AND tablename <> 'alembic_version'
          LOOP
            EXECUTE format('DROP TABLE %I.%I CASCADE', schema_name, object_record.tablename);
          END LOOP;
          FOR object_record IN
            SELECT p.proname AS routine_name,
                   pg_get_function_identity_arguments(p.oid) AS identity_arguments
              FROM pg_proc AS p
              JOIN pg_namespace AS n ON n.oid = p.pronamespace
             WHERE n.nspname = schema_name AND p.prokind IN ('f', 'p')
          LOOP
            EXECUTE format(
              'DROP ROUTINE %I.%I(%s) CASCADE',
              schema_name,
              object_record.routine_name,
              object_record.identity_arguments
            );
          END LOOP;
        END
        $$;
        """
    )
