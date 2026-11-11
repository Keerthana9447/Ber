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
    op.drop_constraint("invoice_items_warehouse_id_fkey", "invoice_items", type_="foreignkey")
    op.drop_column("invoice_items", "warehouse_id")
    op.drop_column("invoices", "hsn_summary")
    for column in (
        "seller_authorized_signatory",
        "seller_terms_and_conditions",
        "seller_upi_id",
        "seller_bank_ifsc",
        "seller_bank_account_no",
        "seller_bank_name",
        "seller_logo_path",
        "seller_address",
        "seller_pan",
        "seller_trade_name",
        "seller_legal_name",
        "customer_name",
    ):
        op.drop_column("invoices", column)
    op.drop_index("invoices_issue_idempotency_uq", table_name="invoices")
    op.drop_column("invoices", "issue_idempotency_key")
    op.drop_column("company_settings", "allow_invoice_issue_without_approval")
