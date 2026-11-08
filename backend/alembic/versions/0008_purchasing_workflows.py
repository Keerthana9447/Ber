"""Add GRN serial/cost snapshots and purchase-chain links.

Revision ID: 0008_purchasing_workflows
Revises: 0007_weighted_average_cost
"""
from __future__ import annotations

from alembic import op

revision = "0008_purchasing_workflows"
down_revision = "0007_weighted_average_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE purchase_items
          ADD COLUMN source_purchase_item_id BIGINT REFERENCES purchase_items(id),
          ADD COLUMN cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0;
        CREATE INDEX purchase_items_source_ix ON purchase_items (source_purchase_item_id)
          WHERE source_purchase_item_id IS NOT NULL;

        ALTER TABLE goods_receipt_items
          ADD COLUMN serial_id BIGINT REFERENCES product_serials(id),
          ADD COLUMN unit_cost NUMERIC(14,4);
        UPDATE goods_receipt_items gri
           SET unit_cost = ROUND(pi.taxable_value / pi.quantity, 4)
          FROM purchase_items pi
         WHERE pi.id = gri.purchase_item_id AND gri.unit_cost IS NULL;
        ALTER TABLE goods_receipt_items
          ALTER COLUMN unit_cost SET NOT NULL;
        ALTER TABLE goods_receipt_items
          ADD CONSTRAINT goods_receipt_items_unit_cost_check CHECK (unit_cost >= 0);
        UPDATE purchase_items
           SET cess_rate = CASE
             WHEN taxable_value > 0 THEN ROUND(cess * 100 / taxable_value, 2)
             ELSE 0
           END
         WHERE cess_rate = 0 AND cess > 0;
        CREATE UNIQUE INDEX goods_receipt_items_serial_uq
          ON goods_receipt_items (serial_id) WHERE serial_id IS NOT NULL;

        CREATE TABLE debit_note_items (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          debit_note_id BIGINT NOT NULL REFERENCES debit_notes(id) ON DELETE CASCADE,
          purchase_item_id BIGINT NOT NULL REFERENCES purchase_items(id),
          quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0),
          taxable_value NUMERIC(14,2) NOT NULL CHECK (taxable_value >= 0),
          cgst NUMERIC(14,2) NOT NULL DEFAULT 0,
          sgst NUMERIC(14,2) NOT NULL DEFAULT 0,
          igst NUMERIC(14,2) NOT NULL DEFAULT 0,
          cess NUMERIC(14,2) NOT NULL DEFAULT 0,
          serial_id BIGINT REFERENCES product_serials(id),
          warehouse_id BIGINT REFERENCES warehouses(id),
          unit_cost NUMERIC(14,4) NOT NULL CHECK (unit_cost >= 0)
        );
        CREATE INDEX debit_note_items_note_ix ON debit_note_items (debit_note_id);

        INSERT INTO accounts (code, name, type)
        VALUES
          ('2104', 'Goods Received Not Invoiced', 'liability'),
          ('5191', 'Purchase Price Variance', 'expense')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE debit_note_items;
        DROP INDEX goods_receipt_items_serial_uq;
        ALTER TABLE goods_receipt_items DROP CONSTRAINT goods_receipt_items_unit_cost_check;
        ALTER TABLE goods_receipt_items DROP COLUMN serial_id, DROP COLUMN unit_cost;
        DROP INDEX purchase_items_source_ix;
        ALTER TABLE purchase_items DROP COLUMN source_purchase_item_id;
        ALTER TABLE purchase_items DROP COLUMN cess_rate;
        """
    )
