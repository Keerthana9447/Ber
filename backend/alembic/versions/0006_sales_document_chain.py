"""Add order-linked challan lines and source-order invoice item links.

Revision ID: 0006_sales_document_chain
Revises: 0005_payments_credit_notes
"""
from __future__ import annotations

from alembic import op

revision = "0006_sales_document_chain"
down_revision = "0005_payments_credit_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE quotation_items
          ADD COLUMN IF NOT EXISTS unit TEXT,
          ADD COLUMN IF NOT EXISTS cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0;
        ALTER TABLE sales_order_items
          ADD COLUMN IF NOT EXISTS unit TEXT,
          ADD COLUMN IF NOT EXISTS cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0;
        ALTER TABLE invoice_items
          ADD COLUMN IF NOT EXISTS sales_order_item_id BIGINT REFERENCES sales_order_items(id);
        CREATE INDEX IF NOT EXISTS invoice_items_order_item_ix ON invoice_items (sales_order_item_id)
          WHERE sales_order_item_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS delivery_challan_items (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          challan_id BIGINT NOT NULL REFERENCES delivery_challans(id) ON DELETE CASCADE,
          sales_order_item_id BIGINT NOT NULL REFERENCES sales_order_items(id),
          product_id BIGINT NOT NULL REFERENCES products(id),
          serial_id BIGINT REFERENCES product_serials(id),
          quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0),
          UNIQUE (challan_id, serial_id)
        );
        CREATE INDEX IF NOT EXISTS delivery_challan_items_order_ix ON delivery_challan_items (sales_order_item_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE delivery_challan_items;
        DROP INDEX invoice_items_order_item_ix;
        ALTER TABLE invoice_items DROP COLUMN sales_order_item_id;
        ALTER TABLE sales_order_items DROP COLUMN unit, DROP COLUMN cess_rate;
        ALTER TABLE quotation_items DROP COLUMN unit, DROP COLUMN cess_rate;
        """
    )
