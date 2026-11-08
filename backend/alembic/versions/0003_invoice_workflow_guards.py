"""Allow serial reuse after cancellation and harden issued invoice immutability.

Revision ID: 0003_invoice_workflow_guards
Revises: 0002_invoice_issue_workflow
"""
from __future__ import annotations

from alembic import op

revision = "0003_invoice_workflow_guards"
down_revision = "0002_invoice_issue_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("invoice_items_serial_uq", table_name="invoice_items")
    op.execute(
        "CREATE UNIQUE INDEX invoice_items_inv_serial_uq "
        "ON invoice_items (invoice_id, serial_id) WHERE serial_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'only draft invoices can be deleted'; END IF;
            RETURN OLD;
          END IF;
          IF OLD.status = 'cancelled' THEN
            IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'cancelled invoices cannot be modified'; END IF;
          ELSIF OLD.status = 'issued' THEN
            IF NEW.status NOT IN ('issued','cancelled') THEN RAISE EXCEPTION 'invalid status transition'; END IF;
            IF (to_jsonb(NEW) - ARRAY['status','cancelled_at','cancel_reason']) IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','cancelled_at','cancel_reason']) THEN
              RAISE EXCEPTION 'issued invoice % cannot be modified', OLD.invoice_number;
            END IF;
            IF NEW.status = 'issued' AND
               (NEW.cancelled_at, NEW.cancel_reason) IS DISTINCT FROM (OLD.cancelled_at, OLD.cancel_reason) THEN
              RAISE EXCEPTION 'issued invoices can only be cancelled through the cancellation workflow';
            END IF;
            IF NEW.status = 'cancelled' AND
               (NEW.cancelled_at IS NULL OR NEW.cancel_reason IS NULL OR length(btrim(NEW.cancel_reason)) < 10) THEN
              RAISE EXCEPTION 'cancellation timestamp and reason are required';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX invoice_items_inv_serial_uq; "
        "CREATE UNIQUE INDEX invoice_items_serial_uq ON invoice_items (serial_id) "
        "WHERE serial_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'only draft invoices can be deleted'; END IF;
            RETURN OLD;
          END IF;
          IF OLD.status IN ('issued','cancelled') THEN
            IF (NEW.invoice_number, NEW.customer_id, NEW.invoice_date, NEW.taxable_total, NEW.cgst_total,
                NEW.sgst_total, NEW.igst_total, NEW.cess_total, NEW.round_off, NEW.grand_total)
               IS DISTINCT FROM
               (OLD.invoice_number, OLD.customer_id, OLD.invoice_date, OLD.taxable_total, OLD.cgst_total,
                OLD.sgst_total, OLD.igst_total, OLD.cess_total, OLD.round_off, OLD.grand_total) THEN
              RAISE EXCEPTION 'issued invoice % cannot be modified; cancel and re-issue or use a credit note', OLD.invoice_number;
            END IF;
            IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
              RAISE EXCEPTION 'cancelled invoices cannot be reopened';
            END IF;
            IF OLD.status = 'issued' AND NEW.status NOT IN ('issued','cancelled') THEN
              RAISE EXCEPTION 'invalid status transition';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )
