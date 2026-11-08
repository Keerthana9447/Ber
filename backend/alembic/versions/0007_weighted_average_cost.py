"""Add warehouse-level moving average inventory costs and opening valuations.

Revision ID: 0007_weighted_average_cost
Revises: 0006_sales_document_chain
"""
from __future__ import annotations

from alembic import op

revision = "0007_weighted_average_cost"
down_revision = "0006_sales_document_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE inventory
          ADD COLUMN IF NOT EXISTS average_unit_cost NUMERIC(14,4)
          CHECK (average_unit_cost >= 0);

        CREATE TABLE IF NOT EXISTS inventory_opening_valuations (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          product_id BIGINT NOT NULL,
          warehouse_id BIGINT NOT NULL,
          quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0),
          unit_cost NUMERIC(14,4) NOT NULL CHECK (unit_cost >= 0),
          total_value NUMERIC(14,2) NOT NULL CHECK (total_value >= 0),
          journal_entry_id BIGINT NOT NULL REFERENCES journal_entries(id),
          created_by BIGINT NOT NULL REFERENCES users(id),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (product_id, warehouse_id)
            REFERENCES inventory(product_id, warehouse_id),
          UNIQUE (journal_entry_id, product_id, warehouse_id)
        );
        CREATE INDEX IF NOT EXISTS inventory_opening_valuation_time_ix
          ON inventory_opening_valuations (created_at DESC);
        DROP TRIGGER IF EXISTS inventory_opening_valuations_append_only ON inventory_opening_valuations;
        CREATE TRIGGER inventory_opening_valuations_append_only
          BEFORE UPDATE OR DELETE ON inventory_opening_valuations
          FOR EACH ROW EXECUTE FUNCTION forbid_change();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER inventory_opening_valuations_append_only ON inventory_opening_valuations;
        DROP TABLE inventory_opening_valuations;
        ALTER TABLE inventory DROP COLUMN average_unit_cost;
        """
    )
