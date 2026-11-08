"""Add tax breakdown to expense documents.

Revision ID: 0009_expense_workflow
Revises: 0008_purchasing_workflows
"""
from __future__ import annotations

from alembic import op

revision = "0009_expense_workflow"
down_revision = "0008_purchasing_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE expenses
          ADD COLUMN cgst_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN sgst_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN igst_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN cess_amount NUMERIC(14,2) NOT NULL DEFAULT 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE expenses
          DROP COLUMN cess_amount,
          DROP COLUMN igst_amount,
          DROP COLUMN sgst_amount,
          DROP COLUMN cgst_amount;
        """
    )
