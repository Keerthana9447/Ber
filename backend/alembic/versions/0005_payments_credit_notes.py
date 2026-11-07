"""Add immutable customer receipts and GST credit-note snapshots.

Revision ID: 0005_payments_credit_notes
Revises: 0004_invoice_cancel_ts
"""
from __future__ import annotations

from alembic import op

revision = "0005_payments_credit_notes"
down_revision = "0004_invoice_cancel_ts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE invoices ADD COLUMN seller_state_code CHAR(2) REFERENCES states(code);
        DROP TRIGGER invoices_guard ON invoices;
        UPDATE invoices
           SET seller_state_code = CASE
             WHEN NOT is_inter_state THEN place_of_supply_code
             ELSE (SELECT state_code FROM company_settings WHERE id = 1)
           END;
        CREATE TRIGGER invoices_guard BEFORE UPDATE OR DELETE ON invoices
          FOR EACH ROW EXECUTE FUNCTION invoice_guard();

        ALTER TABLE credit_notes ADD COLUMN hsn_summary JSONB;
        ALTER TABLE credit_notes ALTER COLUMN status SET DEFAULT 'draft';
        ALTER TABLE credit_notes DROP CONSTRAINT credit_notes_status_check;
        ALTER TABLE credit_notes ADD CONSTRAINT credit_notes_status_check
          CHECK (status IN ('draft','issued','cancelled'));
        ALTER TABLE credit_note_items
          ADD COLUMN description TEXT,
          ADD COLUMN hsn_sac TEXT,
          ADD COLUMN unit TEXT,
          ADD COLUMN unit_price NUMERIC(14,4),
          ADD COLUMN discount NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN gst_rate NUMERIC(5,2),
          ADD COLUMN cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
          ADD COLUMN cgst NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN sgst NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN igst NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN cess NUMERIC(14,2) NOT NULL DEFAULT 0,
          ADD COLUMN serial_id BIGINT REFERENCES product_serials(id),
          ADD COLUMN warehouse_id BIGINT REFERENCES warehouses(id),
          ADD COLUMN unit_cost NUMERIC(14,4);
        UPDATE credit_note_items item
           SET description = source.description,
               hsn_sac = source.hsn_sac,
               unit = source.unit,
               unit_price = source.unit_price,
               gst_rate = source.gst_rate,
               cess_rate = source.cess_rate,
               cgst = source.cgst,
               sgst = source.sgst,
               igst = source.igst,
               cess = source.cess,
               serial_id = source.serial_id,
               warehouse_id = source.warehouse_id,
               unit_cost = source.unit_cost
          FROM invoice_items source
         WHERE source.id = item.invoice_item_id;
        ALTER TABLE credit_note_items
          ALTER COLUMN description SET NOT NULL,
          ALTER COLUMN hsn_sac SET NOT NULL,
          ALTER COLUMN unit_price SET NOT NULL,
          ALTER COLUMN gst_rate SET NOT NULL;

        CREATE UNIQUE INDEX payment_reversal_uq ON payments (reverses_payment_id)
          WHERE reverses_payment_id IS NOT NULL;

        CREATE FUNCTION payment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'payments cannot be deleted; reverse the payment'; END IF;
          IF OLD.status = 'reversed' OR NEW.status <> 'reversed'
             OR (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
            RAISE EXCEPTION 'payments are immutable; use the reversal workflow';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM payments WHERE reverses_payment_id = OLD.id) THEN
            RAISE EXCEPTION 'a reversal payment is required before reversing the original';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER payments_guard BEFORE UPDATE OR DELETE ON payments
          FOR EACH ROW EXECUTE FUNCTION payment_guard();

        CREATE FUNCTION payment_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'payment allocations are immutable; reverse the payment'; END $$;
        CREATE TRIGGER payment_allocations_guard BEFORE UPDATE OR DELETE ON payment_allocations
          FOR EACH ROW EXECUTE FUNCTION payment_allocation_guard();

        CREATE FUNCTION credit_note_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'issued credit notes cannot be deleted'; END IF;
            RETURN OLD;
          END IF;
          IF OLD.status <> 'draft' OR NEW.status NOT IN ('draft','issued')
             OR (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
            RAISE EXCEPTION 'issued credit notes are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER credit_notes_guard BEFORE UPDATE OR DELETE ON credit_notes
          FOR EACH ROW EXECUTE FUNCTION credit_note_guard();

        CREATE FUNCTION credit_note_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE s TEXT; note_id BIGINT;
        BEGIN
          note_id := COALESCE(NEW.credit_note_id, OLD.credit_note_id);
          SELECT status INTO s FROM credit_notes WHERE id = note_id;
          IF s = 'issued' OR TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'issued credit note lines are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER credit_note_items_guard BEFORE INSERT OR UPDATE OR DELETE ON credit_note_items
          FOR EACH ROW EXECUTE FUNCTION credit_note_items_guard();

        CREATE FUNCTION check_credit_note_total() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE credited NUMERIC; invoice_total NUMERIC; target_invoice_id BIGINT;
        BEGIN
          target_invoice_id := COALESCE(NEW.invoice_id, OLD.invoice_id);
          SELECT COALESCE(SUM(grand_total),0) INTO credited
            FROM credit_notes WHERE credit_notes.invoice_id = target_invoice_id AND status = 'issued';
          SELECT grand_total INTO invoice_total FROM invoices WHERE id = target_invoice_id;
          IF credited > invoice_total THEN
            RAISE EXCEPTION 'issued credit notes exceed invoice total';
          END IF;
          RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER credit_note_total_ok AFTER INSERT OR UPDATE ON credit_notes
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_credit_note_total();
        """
    )


def downgrade() -> None:
    raise RuntimeError("The financial payment and credit-note migration is irreversible.")
