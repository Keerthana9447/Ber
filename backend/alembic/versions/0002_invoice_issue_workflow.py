"""Add warehouse and immutable issue snapshots for invoice lifecycle.

Revision ID: 0002_invoice_issue_workflow
Revises: 0001_schema_baseline
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_invoice_issue_workflow"
down_revision = "0001_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_settings",
        sa.Column("allow_invoice_issue_without_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("invoices", sa.Column("issue_idempotency_key", sa.Text(), nullable=True))
    op.create_index(
        "invoices_issue_idempotency_uq",
        "invoices",
        ["issue_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("issue_idempotency_key IS NOT NULL"),
    )
    for column in (
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("seller_legal_name", sa.Text(), nullable=True),
        sa.Column("seller_trade_name", sa.Text(), nullable=True),
        sa.Column("seller_pan", sa.Text(), nullable=True),
        sa.Column("seller_address", sa.Text(), nullable=True),
        sa.Column("seller_logo_path", sa.Text(), nullable=True),
        sa.Column("seller_bank_name", sa.Text(), nullable=True),
        sa.Column("seller_bank_account_no", sa.Text(), nullable=True),
        sa.Column("seller_bank_ifsc", sa.Text(), nullable=True),
        sa.Column("seller_upi_id", sa.Text(), nullable=True),
        sa.Column("seller_terms_and_conditions", sa.Text(), nullable=True),
        sa.Column("seller_authorized_signatory", sa.Text(), nullable=True),
        sa.Column("hsn_summary", sa.dialects.postgresql.JSONB(), nullable=True),
    ):
        op.add_column("invoices", column)
    op.add_column("invoice_items", sa.Column("warehouse_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "invoice_items_warehouse_id_fkey",
        "invoice_items",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )


def downgrade() -> None:
    raise RuntimeError("The invoice issue migration is irreversible; restore a deliberate database backup.")
