"""Permit the cancellation workflow to stamp updated_at while preserving issued fields.

Revision ID: 0004_invoice_cancel_ts
Revises: 0003_invoice_workflow_guards
"""
from __future__ import annotations

from alembic import op

revision = "0004_invoice_cancel_ts"
down_revision = "0003_invoice_workflow_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
            IF NEW.status = 'issued' THEN
              IF (to_jsonb(NEW) - ARRAY['status']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status']) THEN
                RAISE EXCEPTION 'issued invoice % cannot be modified', OLD.invoice_number;
              END IF;
            ELSE
              IF (to_jsonb(NEW) - ARRAY['status','cancelled_at','cancel_reason','updated_at']) IS DISTINCT FROM
                 (to_jsonb(OLD) - ARRAY['status','cancelled_at','cancel_reason','updated_at']) THEN
                RAISE EXCEPTION 'issued invoice % cannot be modified during cancellation', OLD.invoice_number;
              END IF;
              IF NEW.cancelled_at IS NULL OR NEW.cancel_reason IS NULL OR length(btrim(NEW.cancel_reason)) < 10 THEN
                RAISE EXCEPTION 'cancellation timestamp and reason are required';
              END IF;
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
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
