-- BEREV ERP - PostgreSQL 14+ schema (DRAFT).
-- NOT EXECUTED against PostgreSQL in the authoring environment (no database
-- available). Run it on a scratch database first, fix any error, and then turn
-- it into an Alembic baseline migration.
-- Money NUMERIC(14,2); quantities NUMERIC(14,3); rates NUMERIC(5,2).
-- Negative stock: inventory.quantity has CHECK (quantity >= 0). Permitting
-- negative stock must be a deliberate migration (drop that CHECK), not a toggle.

BEGIN;

-- ============ reference / config ============
CREATE TABLE states (code CHAR(2) PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE units (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE gst_rates (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rate NUMERIC(5,2) NOT NULL UNIQUE CHECK (rate >= 0 AND rate <= 100),
  description TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE hsn_sac_codes (
  code TEXT PRIMARY KEY CHECK (code ~ '^[0-9]{4,8}$'),
  kind TEXT NOT NULL CHECK (kind IN ('HSN','SAC')),
  description TEXT NOT NULL,
  default_gst_rate NUMERIC(5,2), default_cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE payment_methods (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  ledger_kind TEXT NOT NULL CHECK (ledger_kind IN ('cash','bank')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE branches (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, state_code CHAR(2) NOT NULL REFERENCES states(code),
  address TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE);

-- single-row company profile; every official identifier is configurable and nullable
CREATE TABLE company_settings (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  legal_name TEXT NOT NULL, trade_name TEXT, address TEXT, phone TEXT, email TEXT,
  gstin TEXT, pan TEXT, state_code CHAR(2) REFERENCES states(code),
  bank_name TEXT, bank_account_no TEXT, bank_ifsc TEXT, upi_id TEXT,
  logo_path TEXT, invoice_prefix TEXT NOT NULL DEFAULT 'INV' CHECK (invoice_prefix ~ '^[A-Za-z0-9/-]{1,6}$'),
  fy_start_month SMALLINT NOT NULL DEFAULT 4 CHECK (fy_start_month BETWEEN 1 AND 12),
  allow_invoice_issue_without_approval BOOLEAN NOT NULL DEFAULT FALSE,
  terms_and_conditions TEXT, authorized_signatory TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE number_sequences (series TEXT PRIMARY KEY, last_number BIGINT NOT NULL CHECK (last_number >= 0));

-- ============ identity & access ============
CREATE TABLE roles (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT);
CREATE TABLE permissions (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, code TEXT NOT NULL UNIQUE, description TEXT);
CREATE TABLE role_permissions (
  role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id));
CREATE TABLE users (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email TEXT NOT NULL, full_name TEXT NOT NULL, password_hash TEXT NOT NULL,
  role_id BIGINT NOT NULL REFERENCES roles(id),
  branch_id BIGINT REFERENCES branches(id),
  dealer_id BIGINT,                              -- FK added after dealers
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  failed_login_count INT NOT NULL DEFAULT 0, locked_until TIMESTAMPTZ,
  must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
  last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));
CREATE TABLE refresh_tokens (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE password_reset_tokens (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE login_audit (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT REFERENCES users(id), email_attempted TEXT, success BOOLEAN NOT NULL,
  ip_address INET, user_agent TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX login_audit_user_ix ON login_audit (user_id, created_at DESC);

-- ============ parties ============
CREATE TABLE customers (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  customer_type TEXT NOT NULL CHECK (customer_type IN ('individual','business','dealer','distributor','franchise','corporate')),
  phone TEXT, email TEXT, gstin TEXT, pan TEXT,
  billing_address TEXT, shipping_address TEXT, state_code CHAR(2) REFERENCES states(code), city TEXT,
  opening_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  credit_limit NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit_limit >= 0),
  payment_terms_days INT NOT NULL DEFAULT 0 CHECK (payment_terms_days >= 0),
  branch_id BIGINT REFERENCES branches(id),
  is_active BOOLEAN NOT NULL DEFAULT TRUE, deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX customers_name_ix ON customers (lower(name));
CREATE INDEX customers_phone_ix ON customers (phone);
CREATE UNIQUE INDEX customers_gstin_uq ON customers (gstin) WHERE gstin IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE suppliers (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, phone TEXT, email TEXT, gstin TEXT, pan TEXT,
  address TEXT, state_code CHAR(2) REFERENCES states(code), city TEXT,
  opening_balance NUMERIC(14,2) NOT NULL DEFAULT 0, payment_terms_days INT NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE, deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX suppliers_gstin_uq ON suppliers (gstin) WHERE gstin IS NOT NULL AND deleted_at IS NULL;

-- a dealer is a customer (for invoicing/ledger) plus network attributes
CREATE TABLE dealers (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dealer_code TEXT NOT NULL UNIQUE,
  customer_id BIGINT NOT NULL UNIQUE REFERENCES customers(id),
  territory TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('pending','active','suspended','terminated')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE users ADD CONSTRAINT users_dealer_fk FOREIGN KEY (dealer_id) REFERENCES dealers(id);

-- ============ products & stock ============
CREATE TABLE product_categories (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name TEXT NOT NULL UNIQUE, parent_id BIGINT REFERENCES product_categories(id),
  is_serialized BOOLEAN NOT NULL DEFAULT FALSE, is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE products (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE, code TEXT, name TEXT NOT NULL,
  category_id BIGINT NOT NULL REFERENCES product_categories(id),
  brand TEXT, model TEXT, description TEXT,
  hsn_sac TEXT NOT NULL REFERENCES hsn_sac_codes(code),
  unit TEXT NOT NULL REFERENCES units(code),
  purchase_price NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (purchase_price >= 0),
  selling_price NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (selling_price >= 0),
  gst_rate NUMERIC(5,2) NOT NULL, cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
  min_stock NUMERIC(14,3) NOT NULL DEFAULT 0,
  is_serialized BOOLEAN NOT NULL DEFAULT FALSE, is_batch_tracked BOOLEAN NOT NULL DEFAULT FALSE,
  is_service BOOLEAN NOT NULL DEFAULT FALSE, image_path TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE, deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (NOT (is_service AND is_serialized)));
CREATE INDEX products_name_ix ON products (lower(name));
CREATE TABLE dealer_prices (
  dealer_id BIGINT NOT NULL REFERENCES dealers(id), product_id BIGINT NOT NULL REFERENCES products(id),
  price NUMERIC(14,2) NOT NULL CHECK (price >= 0), PRIMARY KEY (dealer_id, product_id));
CREATE TABLE warehouses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  branch_id BIGINT REFERENCES branches(id), code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE inventory (
  product_id BIGINT NOT NULL REFERENCES products(id), warehouse_id BIGINT NOT NULL REFERENCES warehouses(id),
  quantity NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  PRIMARY KEY (product_id, warehouse_id));
CREATE TABLE batches (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES products(id), batch_no TEXT NOT NULL,
  mfg_date DATE, expiry_date DATE, UNIQUE (product_id, batch_no));

-- ============ purchasing (request -> order -> invoice) ============
CREATE TABLE purchases (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('purchase_request','purchase_order','purchase_invoice')),
  number TEXT NOT NULL, supplier_id BIGINT REFERENCES suppliers(id),
  supplier_invoice_no TEXT, doc_date DATE NOT NULL, due_date DATE,
  branch_id BIGINT REFERENCES branches(id), place_of_supply_code CHAR(2) REFERENCES states(code),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','ordered','received','posted','cancelled')),
  taxable_total NUMERIC(14,2) NOT NULL DEFAULT 0, cgst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  sgst_total NUMERIC(14,2) NOT NULL DEFAULT 0, igst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  cess_total NUMERIC(14,2) NOT NULL DEFAULT 0, round_off NUMERIC(14,2) NOT NULL DEFAULT 0,
  grand_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  parent_id BIGINT REFERENCES purchases(id),       -- request -> order -> invoice chain
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (doc_type, number));
CREATE UNIQUE INDEX purchases_supplier_inv_uq ON purchases (supplier_id, supplier_invoice_no)
  WHERE doc_type = 'purchase_invoice' AND supplier_invoice_no IS NOT NULL AND status <> 'cancelled';
CREATE TABLE purchase_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  purchase_id BIGINT NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
  product_id BIGINT NOT NULL REFERENCES products(id), description TEXT, hsn_sac TEXT NOT NULL,
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), unit_price NUMERIC(14,4) NOT NULL CHECK (unit_price >= 0),
  discount NUMERIC(14,2) NOT NULL DEFAULT 0, taxable_value NUMERIC(14,2) NOT NULL,
  gst_rate NUMERIC(5,2) NOT NULL, cgst NUMERIC(14,2) NOT NULL DEFAULT 0, sgst NUMERIC(14,2) NOT NULL DEFAULT 0,
  igst NUMERIC(14,2) NOT NULL DEFAULT 0, cess NUMERIC(14,2) NOT NULL DEFAULT 0);
CREATE TABLE goods_receipts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  grn_number TEXT NOT NULL UNIQUE, purchase_id BIGINT NOT NULL REFERENCES purchases(id),
  warehouse_id BIGINT NOT NULL REFERENCES warehouses(id), received_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','cancelled')),
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE goods_receipt_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  goods_receipt_id BIGINT NOT NULL REFERENCES goods_receipts(id) ON DELETE CASCADE,
  purchase_item_id BIGINT NOT NULL REFERENCES purchase_items(id),
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), batch_id BIGINT REFERENCES batches(id));

-- EV serial register. Uniqueness enforced only where the identifier exists.
CREATE TABLE product_serials (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES products(id),
  chassis_no TEXT, vin TEXT, motor_no TEXT, battery_serial TEXT,
  battery_chemistry TEXT, battery_capacity_kwh NUMERIC(8,2), variant TEXT,
  mfg_date DATE, warranty_start DATE, warranty_months INT CHECK (warranty_months >= 0),
  supplier_id BIGINT REFERENCES suppliers(id), purchase_item_id BIGINT REFERENCES purchase_items(id),
  warehouse_id BIGINT REFERENCES warehouses(id),
  status TEXT NOT NULL DEFAULT 'available'
    CHECK (status IN ('available','reserved','sold','delivered','returned','under_service','transferred')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (chassis_no IS NOT NULL OR motor_no IS NOT NULL OR battery_serial IS NOT NULL OR vin IS NOT NULL));
CREATE UNIQUE INDEX serial_chassis_uq ON product_serials (upper(chassis_no)) WHERE chassis_no IS NOT NULL;
CREATE UNIQUE INDEX serial_vin_uq ON product_serials (upper(vin)) WHERE vin IS NOT NULL;
CREATE UNIQUE INDEX serial_motor_uq ON product_serials (upper(motor_no)) WHERE motor_no IS NOT NULL;
CREATE UNIQUE INDEX serial_battery_uq ON product_serials (upper(battery_serial)) WHERE battery_serial IS NOT NULL;
CREATE INDEX serial_status_ix ON product_serials (product_id, status);

CREATE TABLE stock_movements (   -- append-only ledger; inventory is a cache of its sum
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES products(id), warehouse_id BIGINT NOT NULL REFERENCES warehouses(id),
  serial_id BIGINT REFERENCES product_serials(id), batch_id BIGINT REFERENCES batches(id),
  movement_type TEXT NOT NULL CHECK (movement_type IN
    ('purchase_in','sale_out','adjustment','transfer_in','transfer_out','purchase_return','sales_return','opening')),
  quantity_change NUMERIC(14,3) NOT NULL CHECK (quantity_change <> 0),
  unit_cost NUMERIC(14,4), ref_type TEXT, ref_id BIGINT, notes TEXT,
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX stock_mov_prod_ix ON stock_movements (product_id, created_at DESC);
CREATE INDEX stock_mov_ref_ix ON stock_movements (ref_type, ref_id);

-- ============ sales (quotation -> order -> challan -> invoice) ============
CREATE TABLE quotations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  number TEXT NOT NULL UNIQUE, customer_id BIGINT NOT NULL REFERENCES customers(id),
  quote_date DATE NOT NULL, valid_until DATE, place_of_supply_code CHAR(2) NOT NULL REFERENCES states(code),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','accepted','rejected','expired','converted')),
  salesperson_id BIGINT REFERENCES users(id), branch_id BIGINT REFERENCES branches(id),
  taxable_total NUMERIC(14,2) NOT NULL DEFAULT 0, tax_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  round_off NUMERIC(14,2) NOT NULL DEFAULT 0, grand_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE quotation_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  quotation_id BIGINT NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
  product_id BIGINT NOT NULL REFERENCES products(id), description TEXT, hsn_sac TEXT NOT NULL,
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), unit_price NUMERIC(14,4) NOT NULL,
  discount NUMERIC(14,2) NOT NULL DEFAULT 0, taxable_value NUMERIC(14,2) NOT NULL, gst_rate NUMERIC(5,2) NOT NULL);
CREATE TABLE sales_orders (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  number TEXT NOT NULL UNIQUE, customer_id BIGINT NOT NULL REFERENCES customers(id),
  quotation_id BIGINT REFERENCES quotations(id), order_date DATE NOT NULL,
  place_of_supply_code CHAR(2) NOT NULL REFERENCES states(code),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed','partially_delivered','delivered','invoiced','cancelled')),
  salesperson_id BIGINT REFERENCES users(id), branch_id BIGINT REFERENCES branches(id),
  taxable_total NUMERIC(14,2) NOT NULL DEFAULT 0, tax_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  round_off NUMERIC(14,2) NOT NULL DEFAULT 0, grand_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE sales_order_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sales_order_id BIGINT NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
  product_id BIGINT NOT NULL REFERENCES products(id), description TEXT, hsn_sac TEXT NOT NULL,
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), unit_price NUMERIC(14,4) NOT NULL,
  discount NUMERIC(14,2) NOT NULL DEFAULT 0, taxable_value NUMERIC(14,2) NOT NULL, gst_rate NUMERIC(5,2) NOT NULL);
CREATE TABLE delivery_challans (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  number TEXT NOT NULL UNIQUE, sales_order_id BIGINT REFERENCES sales_orders(id),
  customer_id BIGINT NOT NULL REFERENCES customers(id), challan_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','dispatched','delivered','cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE invoices (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doc_type TEXT NOT NULL DEFAULT 'tax_invoice' CHECK (doc_type IN ('tax_invoice','bill_of_supply','proforma')),
  invoice_number TEXT,                              -- assigned only at issue
  issue_idempotency_key TEXT,
  fy_start_year INT, customer_id BIGINT NOT NULL REFERENCES customers(id),
  sales_order_id BIGINT REFERENCES sales_orders(id), branch_id BIGINT REFERENCES branches(id),
  salesperson_id BIGINT REFERENCES users(id),
  invoice_date DATE NOT NULL, due_date DATE,
  seller_gstin TEXT, customer_gstin TEXT, billing_address TEXT, shipping_address TEXT,  -- snapshots
  customer_name TEXT, seller_legal_name TEXT, seller_trade_name TEXT, seller_pan TEXT,
  seller_address TEXT, seller_logo_path TEXT, seller_bank_name TEXT, seller_bank_account_no TEXT,
  seller_bank_ifsc TEXT, seller_upi_id TEXT, seller_terms_and_conditions TEXT,
  seller_authorized_signatory TEXT, hsn_summary JSONB,
  place_of_supply_code CHAR(2) NOT NULL REFERENCES states(code),
  is_inter_state BOOLEAN NOT NULL, reverse_charge BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','issued','cancelled')),
  taxable_total NUMERIC(14,2) NOT NULL DEFAULT 0, cgst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  sgst_total NUMERIC(14,2) NOT NULL DEFAULT 0, igst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  cess_total NUMERIC(14,2) NOT NULL DEFAULT 0, round_off NUMERIC(14,2) NOT NULL DEFAULT 0,
  grand_total NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (grand_total >= 0),
  payment_terms TEXT, notes TEXT,
  approved_by BIGINT REFERENCES users(id), issued_by BIGINT REFERENCES users(id), issued_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ, cancel_reason TEXT, created_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status = 'draft' OR status = 'approved' OR invoice_number IS NOT NULL OR doc_type = 'proforma'),
  CHECK (NOT is_inter_state OR (cgst_total = 0 AND sgst_total = 0)),
  CHECK (is_inter_state OR igst_total = 0),
  CHECK (grand_total = taxable_total + cgst_total + sgst_total + igst_total + cess_total + round_off));
CREATE UNIQUE INDEX invoices_number_uq ON invoices (invoice_number) WHERE invoice_number IS NOT NULL;
CREATE UNIQUE INDEX invoices_issue_idempotency_uq ON invoices (issue_idempotency_key)
  WHERE issue_idempotency_key IS NOT NULL;
CREATE INDEX invoices_cust_ix ON invoices (customer_id, invoice_date DESC);
CREATE INDEX invoices_date_ix ON invoices (invoice_date);
CREATE INDEX invoices_status_ix ON invoices (status);
CREATE TABLE invoice_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  invoice_id BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  product_id BIGINT REFERENCES products(id), serial_id BIGINT REFERENCES product_serials(id),
  warehouse_id BIGINT REFERENCES warehouses(id),
  description TEXT NOT NULL, hsn_sac TEXT NOT NULL, unit TEXT,
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), unit_price NUMERIC(14,4) NOT NULL CHECK (unit_price >= 0),
  discount NUMERIC(14,2) NOT NULL DEFAULT 0, taxable_value NUMERIC(14,2) NOT NULL,
  gst_rate NUMERIC(5,2) NOT NULL, cess_rate NUMERIC(5,2) NOT NULL DEFAULT 0,
  cgst NUMERIC(14,2) NOT NULL DEFAULT 0, sgst NUMERIC(14,2) NOT NULL DEFAULT 0,
  igst NUMERIC(14,2) NOT NULL DEFAULT 0, cess NUMERIC(14,2) NOT NULL DEFAULT 0,
  unit_cost NUMERIC(14,4));                         -- cost snapshot for COGS
CREATE INDEX invoice_items_inv_ix ON invoice_items (invoice_id);
CREATE UNIQUE INDEX invoice_items_inv_serial_uq ON invoice_items (invoice_id, serial_id)
  WHERE serial_id IS NOT NULL;

CREATE TABLE credit_notes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  note_number TEXT NOT NULL UNIQUE, invoice_id BIGINT NOT NULL REFERENCES invoices(id),
  note_date DATE NOT NULL, reason TEXT NOT NULL,
  taxable_total NUMERIC(14,2) NOT NULL, cgst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  sgst_total NUMERIC(14,2) NOT NULL DEFAULT 0, igst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  cess_total NUMERIC(14,2) NOT NULL DEFAULT 0, round_off NUMERIC(14,2) NOT NULL DEFAULT 0,
  grand_total NUMERIC(14,2) NOT NULL CHECK (grand_total > 0),
  restock BOOLEAN NOT NULL DEFAULT FALSE, status TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','cancelled')),
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE credit_note_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  credit_note_id BIGINT NOT NULL REFERENCES credit_notes(id) ON DELETE CASCADE,
  invoice_item_id BIGINT NOT NULL REFERENCES invoice_items(id),
  quantity NUMERIC(14,3) NOT NULL CHECK (quantity > 0), taxable_value NUMERIC(14,2) NOT NULL);
-- debit notes are raised against PURCHASE invoices (purchase returns / supplier adjustments)
CREATE TABLE debit_notes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  note_number TEXT NOT NULL UNIQUE, purchase_id BIGINT NOT NULL REFERENCES purchases(id),
  note_date DATE NOT NULL, reason TEXT NOT NULL,
  taxable_total NUMERIC(14,2) NOT NULL, cgst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  sgst_total NUMERIC(14,2) NOT NULL DEFAULT 0, igst_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  cess_total NUMERIC(14,2) NOT NULL DEFAULT 0, round_off NUMERIC(14,2) NOT NULL DEFAULT 0,
  grand_total NUMERIC(14,2) NOT NULL CHECK (grand_total > 0),
  status TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','cancelled')),
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now());

-- ============ payments ============
CREATE TABLE payments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  payment_number TEXT NOT NULL UNIQUE,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  customer_id BIGINT REFERENCES customers(id), supplier_id BIGINT REFERENCES suppliers(id),
  method_id BIGINT NOT NULL REFERENCES payment_methods(id),
  amount NUMERIC(14,2) NOT NULL CHECK (amount > 0), payment_date DATE NOT NULL,
  reference_no TEXT, is_advance BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'recorded' CHECK (status IN ('recorded','reversed')),
  reverses_payment_id BIGINT REFERENCES payments(id),
  gateway_txn_id TEXT UNIQUE, gateway_signature_verified BOOLEAN NOT NULL DEFAULT FALSE,
  bank_reconciled BOOLEAN NOT NULL DEFAULT FALSE, bank_reconciled_at TIMESTAMPTZ,
  notes TEXT, branch_id BIGINT REFERENCES branches(id),
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((customer_id IS NOT NULL)::int + (supplier_id IS NOT NULL)::int = 1));
-- direction vs party (customer 'in' / supplier 'out') is enforced in the service layer because
-- refunds and reversals legitimately flip direction.
CREATE INDEX payments_cust_ix ON payments (customer_id, payment_date);
CREATE INDEX payments_supp_ix ON payments (supplier_id, payment_date);
CREATE TABLE payment_allocations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  payment_id BIGINT NOT NULL REFERENCES payments(id),
  invoice_id BIGINT REFERENCES invoices(id), purchase_id BIGINT REFERENCES purchases(id),
  amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((invoice_id IS NOT NULL)::int + (purchase_id IS NOT NULL)::int = 1));
CREATE INDEX alloc_invoice_ix ON payment_allocations (invoice_id);
CREATE INDEX alloc_purchase_ix ON payment_allocations (purchase_id);

-- ============ expenses ============
CREATE TABLE expense_categories (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  account_code TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE expenses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  expense_number TEXT NOT NULL UNIQUE, expense_date DATE NOT NULL,
  category_id BIGINT NOT NULL REFERENCES expense_categories(id), description TEXT,
  amount NUMERIC(14,2) NOT NULL CHECK (amount > 0), gst_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  vendor TEXT, method_id BIGINT REFERENCES payment_methods(id), reference_no TEXT, attachment_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','posted','cancelled')),
  approved_by BIGINT REFERENCES users(id), branch_id BIGINT REFERENCES branches(id),
  created_by BIGINT REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now());

-- ============ accounting ============
CREATE TABLE accounts (
  code TEXT PRIMARY KEY, name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','cogs','expense')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE journal_entries (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entry_date DATE NOT NULL, reference TEXT NOT NULL, narration TEXT,
  source_type TEXT, source_id BIGINT,
  reverses_entry_id BIGINT REFERENCES journal_entries(id),
  branch_id BIGINT REFERENCES branches(id), posted_by BIGINT REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX je_date_ix ON journal_entries (entry_date);
CREATE INDEX je_source_ix ON journal_entries (source_type, source_id);
CREATE TABLE journal_entry_lines (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entry_id BIGINT NOT NULL REFERENCES journal_entries(id),
  account_code TEXT NOT NULL REFERENCES accounts(code),
  debit NUMERIC(14,2) NOT NULL DEFAULT 0, credit NUMERIC(14,2) NOT NULL DEFAULT 0,
  customer_id BIGINT REFERENCES customers(id), supplier_id BIGINT REFERENCES suppliers(id),
  CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)));
CREATE INDEX jel_acct_ix ON journal_entry_lines (account_code);
CREATE INDEX jel_entry_ix ON journal_entry_lines (entry_id);

-- ============ notifications & audit ============
CREATE TABLE notifications (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT REFERENCES users(id), role_id BIGINT REFERENCES roles(id),
  kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT, link TEXT,
  is_read BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX notif_user_ix ON notifications (user_id, is_read, created_at DESC);
CREATE TABLE audit_logs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id BIGINT REFERENCES users(id), action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id BIGINT,
  old_values JSONB, new_values JSONB,      -- application must strip secrets/hashes before writing
  ip_address INET, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX audit_entity_ix ON audit_logs (entity_type, entity_id);
CREATE INDEX audit_time_ix ON audit_logs (created_at DESC);

-- ============ integrity triggers ============
CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% on % is not allowed (append-only; use a reversal)', TG_OP, TG_TABLE_NAME; END $$;
CREATE TRIGGER je_append_only BEFORE UPDATE OR DELETE ON journal_entries FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER jel_append_only BEFORE UPDATE OR DELETE ON journal_entry_lines FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER stock_mov_append_only BEFORE UPDATE OR DELETE ON stock_movements FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER login_audit_append_only BEFORE UPDATE OR DELETE ON login_audit FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- every journal entry must balance at COMMIT time
CREATE FUNCTION check_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d NUMERIC; c NUMERIC; n INT;
BEGIN
  SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0), COUNT(*) INTO d, c, n
    FROM journal_entry_lines WHERE entry_id = NEW.entry_id;
  IF d <> c OR n < 2 THEN
    RAISE EXCEPTION 'journal entry % is unbalanced (debit %, credit %, lines %)', NEW.entry_id, d, c, n;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER je_balanced AFTER INSERT ON journal_entry_lines
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_entry_balanced();

-- issued/cancelled invoices: money fields frozen, only issued -> cancelled allowed, never deleted
CREATE FUNCTION invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
        RAISE EXCEPTION 'issued invoice % cannot be modified; cancel and re-issue or use a credit note', OLD.invoice_number;
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
CREATE TRIGGER invoices_guard BEFORE UPDATE OR DELETE ON invoices FOR EACH ROW EXECUTE FUNCTION invoice_guard();

CREATE FUNCTION invoice_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s TEXT; inv BIGINT;
BEGIN
  inv := COALESCE(NEW.invoice_id, OLD.invoice_id);
  SELECT status INTO s FROM invoices WHERE id = inv;
  IF s IS NOT NULL AND s IN ('issued','cancelled') THEN
    RAISE EXCEPTION 'items of an % invoice cannot be changed', s; END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER invoice_items_guard BEFORE INSERT OR UPDATE OR DELETE ON invoice_items
  FOR EACH ROW EXECUTE FUNCTION invoice_items_guard();

-- allocations must never exceed the payment amount (checked at COMMIT)
CREATE FUNCTION check_allocation_total() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a NUMERIC; p NUMERIC;
BEGIN
  SELECT COALESCE(SUM(amount),0) INTO a FROM payment_allocations WHERE payment_id = NEW.payment_id;
  SELECT amount INTO p FROM payments WHERE id = NEW.payment_id;
  IF a > p THEN RAISE EXCEPTION 'allocations (%) exceed payment amount (%)', a, p; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER alloc_total_ok AFTER INSERT ON payment_allocations
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_allocation_total();

COMMIT;
