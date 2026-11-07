# API contract the UI expects

The UI only calls real endpoints. The backend provides `/health`, `/auth/*`, `/dashboard`, settings, customers,
products/inventory, invoice lifecycle, payments/refunds, credit notes, and customer ledgers. Unimplemented
modules show a "not available yet" state; no business data is faked.

All paths are under `/api/v1`. The UI sends `Authorization: Bearer <access_token>` and never computes
money or tax; it only formats what the server returns. Money values should be decimal **strings**.

## Lists (customers, suppliers, dealers, products, inventory, quotations, orders, invoices, purchases, payments, expenses, accounting, reports, users)

`GET /<module>?page=1&page_size=20&q=<search>` -> `{ "items": [ {...} ], "total": 123 }`
(a bare array also works; the UI then pages it in the browser).

Column keys read per module (missing keys render as a dash). Edit `src/lib/modules.ts` to match the real fields.

| Module | Keys |
|---|---|
| quotations | number, customer_name, quote_date, valid_until, status, grand_total |
| orders | number, customer_name, order_date, status, grand_total |
| invoices | invoice_number, customer_name, invoice_date, due_date, status, payment_status, grand_total |
| customers | code, name, customer_type, phone, city, credit_limit |
| dealers | dealer_code, name, territory, status |
| purchases | number, doc_type, supplier_name, doc_date, status, grand_total |
| suppliers | code, name, gstin, phone, city |
| products | sku, name, brand, hsn_sac, gst_rate, selling_price, is_active |
| inventory | sku, product_name, warehouse_name, quantity |
| payments | payment_number, party_name, direction, payment_date, method, amount |
| expenses | expense_number, expense_date, category_name, vendor, status, amount |
| accounting | reference, entry_date, narration, total_debit, total_credit |
| reports | name, description |
| users | full_name, email, role, is_active, last_login_at |

## Dashboard

`GET /dashboard?from=YYYY-MM-DD&to=YYYY-MM-DD` ->

```json
{
  "kpis": { "sales_today": "0.00", "sales_month": "0.00", "purchases_total": "0.00", "revenue_total": "0.00",
            "expenses_total": "0.00", "gross_profit": "0.00", "receivables_outstanding": "0.00",
            "payables_outstanding": "0.00", "customers_total": 0, "suppliers_total": 0, "products_total": 0,
            "low_stock_products": 0, "ev_vehicles_in_stock": 0 },
  "sales_trend": [{ "date": "2026-11-01", "amount": "0.00" }],
  "purchase_trend": [{ "date": "2026-11-01", "amount": "0.00" }],
  "revenue_vs_expenses": [{ "period": "Nov 2026", "revenue": "0.00", "expenses": "0.00" }],
  "top_products": [{ "name": "", "quantity": 0, "amount": "0.00" }],
  "payment_methods": [{ "method": "UPI", "amount": "0.00" }],
  "recent_invoices": [{ "invoice_number": "", "customer_name": "", "invoice_date": "", "grand_total": "0.00", "status": "" }],
  "recent_transactions": [{ "reference": "", "date": "", "description": "", "amount": "0.00" }]
}
```

`GET /dashboard?from=YYYY-MM-DD&to=YYYY-MM-DD` requires `dashboard:read`. The inclusive date range is limited
to 366 days. Revenue includes issued non-proforma invoices; purchases include posted purchase invoices;
expenses include posted expenses. Receivables and payables are outstanding balances after recorded
allocations and issued credit/debit notes.

## Customers

- `GET /customers?page=1&page_size=20&q=<search>` returns `{ "items": [...], "total": n }`.
- `GET /customers/{id}` returns one active, non-deleted customer.
- `POST /customers` creates a customer (`customers:create`).
- `PUT /customers/{id}` replaces the customer's editable fields (`customers:update`).
- `DELETE /customers/{id}` soft-deletes the customer and returns 204 (`customers:delete`, Super Admin/Admin).
- Create/update requests use `code`, `name`, `customer_type`, and optionally `phone`, `email`, `gstin`, `pan`,
  `billing_address`, `shipping_address`, `state_code`, `city`, `opening_balance`, `credit_limit`,
  `payment_terms_days`, `branch_id`, and `is_active`. Monetary values are decimal strings.
- Duplicate codes/GSTINs return 409. Unknown customer IDs return 404. Deleted customers are omitted from reads;
  deletion preserves their history and writes an audit record.

## Products and inventory

- `GET /product-options` returns active categories, HSN/SAC codes, units, GST rates, and warehouses for forms.
- `POST /product-categories` requires `products:manage`.
- `POST /gst-rates` and `POST /hsn-sac-codes` require `settings:manage`. GST rates are administrator-configured;
  the application does not invent or certify tax rates. Product GST/cess rates must match the selected HSN/SAC defaults.
- `POST /warehouses` requires `inventory:manage`.
- `GET /products` and `GET /products/{id}` require `products:read`; `POST /products`, `PUT /products/{id}`, and
  `DELETE /products/{id}` require `products:manage`. Deletion is soft to preserve transaction history.
- Product writes require an active category, HSN/SAC code, unit, and configured active GST rate. Service products
  must use SAC codes; stock products must use HSN codes. Money and quantity values are decimal strings.
- `GET /inventory?page=1&page_size=20&q=<search>&warehouse_id=<id>` returns stock by product and warehouse.
- `POST /inventory/adjustments` requires `inventory:manage` and accepts a signed `quantity_delta` (up to 3 decimal
  places). A single database transaction locks the product and stock row, rejects negative stock, updates the
  current stock cache, and appends an immutable stock movement. Serialized and batch-tracked items require their
  serial/batch details and are not accepted by this manual adjustment endpoint.

## Invoices, payments, and credit notes

- Invoice draft, preview, approval, issue, cancellation, and PDF endpoints require the corresponding `sales:*`
  permissions. Preview and invoice values are computed/stored by the backend; the frontend only displays them.
- `GET /invoices/{id}/payments` returns the invoice's received, credited, outstanding, customer-credit, payment
  status, and allocated payment history.
- `GET /payment-methods` requires `payments:read`. `POST /payments` records a receipt/advance and accepts
  optional explicit allocations; omitting allocations uses oldest-first. `POST /payments/refunds` requires
  `payments:manage` and refunds only available advance credit or credit on a selected issued invoice.
- `POST /payments/{id}/reverse` posts an accounting reversal and retains both records. `GET /payments/{id}/pdf`
  downloads the authenticated stored receipt PDF.
- `GET /credit-notes` and `GET /credit-notes/{id}` require `sales:read`. `POST /credit-notes` requires
  `sales:create`, computes GST from the original invoice, optionally restocks to its original warehouse/serial,
  and posts accounting entries. Credit-note PDFs use stored values.
- `GET /customers/{id}/ledger?from=YYYY-MM-DD&to=YYYY-MM-DD&page=1&page_size=50` requires `customers:read`
  and returns opening balance, paged transaction rows, running balances, and closing balance. The customer
  master opening-balance field is not included because it has no debit/credit direction.
- Gateway receipts are disabled unless `GATEWAY_WEBHOOK_SECRET` is configured; the webhook requires a valid
  HMAC-SHA256 signature.

## Other
- `GET /notifications?page_size=10` -> `{ "items": [{ "id", "title", "body", "is_read" }] }`
- `GET /settings` -> flat object of company settings (scalars are shown read-only).
- Errors: FastAPI `{ "detail": "message" }` or `{ "detail": [{ "msg": "..." }] }`.
- Production: serve the built UI and the API from one origin (reverse proxy `/api` to FastAPI), or add CORS in the backend.
