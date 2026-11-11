# MASTER BUILD PROMPT — BEREV BUSINESS ERP & GST BILLING SOFTWARE

Act as a Principal Software Architect, Senior Full-Stack Engineer, ERP Product Designer, Database Architect, Security Engineer, and QA Engineer.

Your task is to design, develop, test, and deliver a complete, production-oriented Business Management ERP and GST Billing Software for:

**Company:** BEREV — Boundless EV Revolution  
**Website:** https://berev.world/  
**Business domain:** Electric Vehicle / Smart Mobility  
**Office:** Krishnakanth Complex, Shop No. 15, 1st Floor, Birla Compound, Kurnool, Andhra Pradesh – 518002  
**Email:** admin@berev.world  
**Phone:** +91-9440420255

## 1. PRODUCT OBJECTIVE

Build a complete business application that BEREV can use for its actual daily business operations.

This must NOT be a static dashboard, UI prototype, dummy application, or basic invoice generator.

Build a fully functional, database-driven ERP with:

- GST billing and invoicing
- Sales and purchase management
- Inventory and stock management
- EV vehicle and spare-parts management
- Customer and supplier management
- Dealer and distributor management
- Payment and outstanding tracking
- Expense management
- Financial reports
- Business analytics
- User and role management

All modules must work together using real database operations.

Do not assume BEREV currently sells every product mentioned in its website roadmap. Make products, services, inventory categories, and business modules configurable by the administrator.

## 2. TECHNOLOGY STACK

Use a reliable, maintainable architecture.

Frontend:
- React.js
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui
- Lucide icons
- Recharts
- React Router
- TanStack Query
- React Hook Form
- Zod validation

Backend:
- Python FastAPI
- SQLAlchemy ORM
- Pydantic
- Alembic migrations
- REST API architecture
- JWT authentication
- Role-based access control

Database:
- PostgreSQL
- Proper foreign keys and relational constraints
- Database indexes
- Transaction management
- Soft deletion where appropriate

Infrastructure:
- Docker and Docker Compose
- Environment-based configuration
- Production deployment support
- Structured logging
- Automated database backups

Use stable, mutually compatible package versions. Never hardcode API keys, passwords, secrets, or production credentials.

## 3. UI/UX DESIGN

Create a premium enterprise ERP interface.

Design direction:
- Deep forest green / emerald green
- White and warm ivory backgrounds
- Subtle gold accents
- Professional typography
- Clean cards and data tables
- Minimal gradients
- Consistent spacing
- Modern icons
- Responsive layouts

The software should look like a professional commercial ERP, not a college project.

Create:
- Collapsible sidebar
- Top navigation
- Breadcrumbs
- Global search
- Notification center
- User profile menu
- Dashboard widgets
- Filterable tables
- Pagination
- Loading states
- Empty states
- Error states
- Confirmation dialogs
- Toast notifications

The application must work on desktop, tablet, and mobile.

## 4. AUTHENTICATION AND USER MANAGEMENT

Implement:

- Secure login
- Logout
- Password hashing using Argon2 or bcrypt
- JWT access and refresh token handling
- Session expiration
- Password change
- Password reset workflow
- Account activation/deactivation
- Login audit logs

Roles:

1. Super Admin
2. Admin
3. Accountant
4. Sales Manager
5. Sales Executive
6. Inventory Manager
7. Purchase Manager
8. Branch Manager
9. Dealer / Distributor (restricted portal, optional)

Implement granular permissions for viewing, creating, editing, deleting, approving, and exporting records.

Never rely exclusively on frontend permissions. Enforce authorization on every protected backend endpoint.

## 5. MAIN DASHBOARD

Create a real-time business dashboard using actual database information.

Display:

- Total sales today
- Monthly sales
- Total purchases
- Total revenue
- Total expenses
- Gross profit
- Outstanding receivables
- Outstanding payables
- Total customers
- Total suppliers
- Total products
- Low-stock products
- Total EV vehicles in stock
- Recent invoices
- Recent transactions
- Sales trends
- Purchase trends
- Revenue vs expenses
- Top-selling products
- Payment method distribution

Dashboard filters:
- Today
- Yesterday
- Last 7 days
- This month
- Previous month
- This financial year
- Custom date range

Every dashboard metric must be calculated from database records. Do not display fabricated statistics.

## 6. GST BILLING AND INVOICE MANAGEMENT

This is one of the most important modules.

Build a professional GST invoicing engine for Indian businesses.

Invoice types:
- Tax Invoice
- Bill of Supply
- Proforma Invoice
- Credit Note
- Debit Note
- Delivery Challan
- Quotation
- Sales Order

Invoice fields:

- Company name and logo
- Company address
- GSTIN
- Invoice number
- Invoice date
- Customer name
- Billing address
- Shipping address
- Customer GSTIN
- Place of supply
- State code
- Reverse charge applicability
- Item description
- HSN/SAC code
- Quantity
- Unit
- Unit price
- Discount
- Taxable value
- GST rate
- CGST
- SGST
- IGST
- Cess, where applicable
- Round-off
- Grand total
- Amount in words
- Payment terms
- Bank details
- UPI payment information
- Terms and conditions
- Authorized signature

GST calculation logic:

For intra-state transactions:
- CGST = Applicable GST / 2
- SGST = Applicable GST / 2

For inter-state transactions:
- IGST = Applicable GST

Implement:
- Configurable GST rates
- HSN/SAC master
- State-based tax determination
- Discount calculations
- Taxable value calculations
- Correct rounding
- Line-item and invoice-level validation
- Tax breakdown
- Tax summaries

Never hardcode a single GST rate for all products. Allow authorized administrators to configure applicable tax rates and classifications.

Validate all calculations using unit tests and document the applicable assumptions. Do not claim legal GST compliance without verification by a qualified Indian tax professional.

Invoice numbering:
- Financial-year-based numbering
- Configurable prefixes
- Sequential numbering
- Collision-safe number generation
- No duplicate invoice numbers
- No silent modification of issued invoices

Invoice operations:
- Create
- Edit draft
- Preview
- Approve
- Issue
- Print
- Download PDF
- Email invoice
- Record payment
- Track outstanding balance
- Cancel using appropriate audit-preserving workflow
- Generate credit/debit notes

Create a premium A4 GST invoice PDF with professional BEREV branding.

Include an optional thermal receipt format for POS billing.

Important: Implement invoice numbering and stock updates transactionally to prevent duplicate numbers and inconsistent inventory.

## 7. SALES MANAGEMENT

Build a complete sales workflow:

Quotation → Sales Order → Delivery Challan → Tax Invoice → Payment → Receipt.

Features:
- Create quotations
- Convert quotations to orders
- Convert orders to invoices
- Add multiple products
- Apply discounts
- Assign salesperson
- Select customer
- Track order status
- Track delivery status
- Partial payments
- Full payments
- Sales returns
- Credit notes
- Sales history

Sales reports:
- Daily sales
- Monthly sales
- Annual sales
- Product-wise sales
- Customer-wise sales
- Salesperson-wise sales
- GST-wise sales
- Branch-wise sales

## 8. EV PRODUCT AND INVENTORY MANAGEMENT

Build inventory specifically suitable for an EV business.

Product categories must be configurable, with support for:

- Electric scooters
- Electric bikes
- EV batteries
- Battery chargers
- Motors
- Controllers
- Spare parts
- Accessories
- Charging equipment
- Services
- Other products

Product master fields:

- Product name
- SKU
- Product code
- Category
- Brand
- Model
- Description
- HSN code
- Unit
- Purchase price
- Selling price
- GST rate
- Minimum stock
- Product image
- Active/inactive status

For serialized EV products, support:

- Chassis number
- VIN, if applicable
- Motor number
- Battery serial number
- Battery chemistry
- Battery capacity
- Model and variant
- Manufacturing date
- Warranty start date
- Warranty duration
- Supplier
- Purchase reference
- Stock location
- Current status

Vehicle statuses:
- Available
- Reserved
- Sold
- Delivered
- Returned
- Under service
- Transferred

Inventory features:
- Stock in
- Stock out
- Stock adjustment
- Stock transfer
- Purchase returns
- Sales returns
- Low-stock alerts
- Stock valuation
- Batch tracking where applicable
- Serial number tracking
- Inventory movement history

Prevent duplicate chassis, motor, and battery serial numbers where uniqueness is required.

Do not allow stock to become negative unless an explicitly authorized configuration permits it.

## 9. PURCHASE AND SUPPLIER MANAGEMENT

Implement the complete purchase workflow:

Purchase Request → Purchase Order → Goods Received → Purchase Invoice → Supplier Payment.

Features:
- Supplier master
- Supplier GSTIN
- Purchase orders
- Purchase invoices
- Goods receipt notes
- Purchase returns
- Supplier credit notes
- Payment tracking
- Outstanding payable tracking
- Purchase history

Purchase reports:
- Supplier-wise purchases
- Product-wise purchases
- Monthly purchases
- GST input tax summaries
- Outstanding supplier payments

Update inventory automatically only when the appropriate goods receipt or purchase posting is completed.

## 10. CUSTOMER MANAGEMENT / CRM

Build customer management with:

- Individual customers
- Business customers
- Dealers
- Distributors
- Franchise partners
- Corporate customers

Customer fields:
- Customer ID
- Name
- Phone
- Email
- GSTIN
- PAN, where required
- Billing address
- Shipping address
- State
- City
- Customer type
- Opening balance
- Credit limit
- Payment terms

Features:
- Customer transaction history
- Sales history
- Outstanding balance
- Payment history
- Customer ledger
- Search and filters
- Customer statements
- Export customer data

## 11. DEALER AND DISTRIBUTOR MANAGEMENT

Create a dealer management module for BEREV's business network.

Features:
- Dealer registration
- Dealer profile
- Dealer code
- Contact details
- GSTIN
- Business address
- Territory assignment
- Dealer status
- Dealer-specific pricing
- Dealer orders
- Dealer invoices
- Dealer payment tracking
- Outstanding balances
- Dealer sales reports

Optional restricted dealer portal:
- View assigned products
- View dealer-specific prices
- Place orders
- View order history
- Download invoices
- View payment statements

Keep dealer-specific access isolated. A dealer must never see another dealer's private records.

## 12. PAYMENT MANAGEMENT

Support:

- Cash
- UPI
- Bank transfer
- NEFT
- RTGS
- IMPS
- Cheque
- Card
- Other configurable methods

Features:
- Customer receipts
- Supplier payments
- Advance payments
- Partial payments
- Multiple payments per invoice
- Payment allocation
- Refunds
- Payment references
- Transaction dates
- Bank reconciliation status

Calculate:
- Total invoice amount
- Total amount received
- Remaining amount
- Overdue amount

Payment statuses:
- Unpaid
- Partially Paid
- Paid
- Overdue
- Refunded

Do not mark payments as successful based only on a user clicking a button. For gateway payments, verify signed server-side payment confirmations. Support manual payment recording with an audit trail.

Keep payment gateway integration optional and configurable.

## 13. EXPENSE MANAGEMENT

Create expense tracking.

Categories:
- Rent
- Salaries
- Electricity
- Transportation
- Marketing
- Maintenance
- Office supplies
- Fuel
- Other expenses

Fields:
- Expense ID
- Date
- Category
- Description
- Amount
- GST details, if applicable
- Vendor
- Payment method
- Reference number
- Attachment/receipt
- Created by

Features:
- Expense approval
- Expense history
- Monthly summaries
- Category-wise reports
- Expense export

## 14. ACCOUNTING AND FINANCIAL REPORTS

Implement a practical accounting foundation.

Include:
- Cash book
- Bank book
- Customer ledger
- Supplier ledger
- Sales register
- Purchase register
- Receivables
- Payables
- Credit notes
- Debit notes
- Expense register
- GST summaries
- Trial balance
- Profit and loss report

Use a proper double-entry accounting model if implementing the general ledger.

Every posted financial transaction must have balanced debit and credit entries.

Never calculate net profit as simply sales minus purchases. Clearly distinguish revenue, cost of goods sold, expenses, taxes, and other accounting adjustments.

Provide CSV and Excel exports.

## 15. REPORTING AND ANALYTICS

Create a dedicated reports module with:

- Sales report
- Purchase report
- Inventory report
- Stock valuation
- GST report
- Customer report
- Dealer report
- Supplier report
- Payment report
- Expense report
- Outstanding report
- Profit and loss report
- Product performance

All reports must support date filters and relevant business filters.

Provide PDF, CSV, and Excel exports.

## 16. NOTIFICATIONS

Implement in-app notifications for:

- Low stock
- New orders
- Pending payments
- Overdue invoices
- Payment received
- Purchase arrivals
- Dealer orders
- Approval requests

Use background jobs for scheduled notifications and reminders.

Email notifications should use configurable SMTP credentials.

Do not send external messages without the relevant configuration and authorization.

## 17. SETTINGS AND COMPANY CONFIGURATION

Create a comprehensive settings module.

Company settings:
- Company name
- Logo
- Address
- Phone
- Email
- GSTIN
- PAN
- State
- State code
- Bank account details
- UPI ID
- Invoice prefix
- Financial year
- Terms and conditions
- Authorized signatory

Other settings:
- GST rates
- HSN/SAC codes
- Units
- Product categories
- Payment methods
- Invoice templates
- User roles
- Permissions
- Branches
- Tax configuration
- Notification configuration

Do not invent BEREV's GSTIN, bank account, PAN, or other official registration details. Leave them configurable.

## 18. SECURITY AND PRODUCTION REQUIREMENTS

This software will contain confidential business and financial information.

Implement:

- Server-side authorization
- Secure password hashing
- Input validation
- SQL injection prevention
- XSS protection
- CSRF protection where applicable
- Rate limiting
- Secure CORS configuration
- HTTPS deployment
- Environment variable management
- Secret management
- Database transactions
- Audit logs
- Login attempt monitoring
- File upload validation
- File size restrictions
- Secure error responses
- Data backup and recovery
- Database migration management

Record audit events for:
- Invoice creation and issuance
- Invoice cancellation
- Payment changes
- Stock adjustments
- Purchase changes
- Customer changes
- User permission changes
- Financial record modifications

Do not expose sensitive information in logs.

Never permanently delete posted financial transactions. Use controlled reversal, cancellation, or correction workflows.

## 19. DATABASE DESIGN

Design normalized PostgreSQL tables for:

- users
- roles
- permissions
- role_permissions
- customers
- suppliers
- dealers
- products
- product_categories
- product_serials
- warehouses
- inventory
- stock_movements
- quotations
- sales_orders
- sales_order_items
- invoices
- invoice_items
- credit_notes
- debit_notes
- purchases
- purchase_items
- goods_receipts
- payments
- payment_allocations
- expenses
- expense_categories
- accounts
- journal_entries
- journal_entry_lines
- notifications
- audit_logs
- company_settings

Add suitable indexes, unique constraints, foreign keys, timestamps, and tenant/branch scoping where appropriate.

Design invoice and payment workflows to be safe under concurrent requests.

## 20. API DESIGN

Use versioned REST endpoints.

Example:

/api/v1/auth
/api/v1/dashboard
/api/v1/users
/api/v1/customers
/api/v1/suppliers
/api/v1/dealers
/api/v1/products
/api/v1/inventory
/api/v1/sales
/api/v1/quotations
/api/v1/orders
/api/v1/invoices
/api/v1/purchases
/api/v1/payments
/api/v1/expenses
/api/v1/accounting
/api/v1/reports
/api/v1/notifications
/api/v1/settings

Implement:
- Consistent response schemas
- Appropriate HTTP status codes
- Pagination
- Search
- Filtering
- Sorting
- Validation errors
- API documentation using Swagger/OpenAPI

## 21. TESTING

Create actual automated tests.

Backend:
- Unit tests
- API integration tests
- Authentication tests
- Authorization tests
- GST calculation tests
- Invoice numbering tests
- Inventory tests
- Payment allocation tests
- Accounting balance tests

Frontend:
- Component tests
- Form validation tests
- Critical workflow tests
- End-to-end tests using Playwright

Critical test scenarios:

1. Create a customer.
2. Create a product with GST classification.
3. Add inventory.
4. Generate a quotation.
5. Convert quotation into a sales order.
6. Generate a GST invoice.
7. Verify CGST/SGST or IGST.
8. Download invoice PDF.
9. Record partial payment.
10. Record remaining payment.
11. Verify outstanding balance becomes zero.
12. Verify stock is deducted correctly.
13. Verify audit logs.
14. Verify a user without permission cannot access restricted endpoints.
15. Verify concurrent invoice creation does not produce duplicate invoice numbers.

All tests must execute against a test database, never production data.

## 22. DEVELOPMENT EXECUTION STRATEGY

Work in phases.

Phase 1:
- Analyze repository
- Create system architecture
- Define database schema
- Configure frontend and backend
- Configure PostgreSQL
- Implement authentication

Phase 2:
- Dashboard
- Customer management
- Supplier management
- Product management
- Inventory

Phase 3:
- Sales
- Purchase
- GST billing
- PDF invoices

Phase 4:
- Payments
- Expenses
- Dealer management
- Accounting

Phase 5:
- Reports
- Notifications
- Settings
- Audit logs

Phase 6:
- Automated tests
- Security review
- Performance optimization
- Deployment
- Documentation

## 23. IMPORTANT IMPLEMENTATION RULES

1. First inspect the existing repository and preserve useful existing code.
2. Do not generate only a plan. Implement the actual application.
3. Build complete frontend and backend functionality.
4. Never use fake API responses as a substitute for backend implementation.
5. Never use localStorage as the primary database.
6. Never hardcode financial values or business credentials.
7. Never leave buttons non-functional.
8. Never create non-functional placeholder modules.
9. Every form must have proper validation.
10. Every important operation must have loading, success, and error states.
11. Every API operation must have appropriate authorization.
12. Use database transactions for financial and inventory operations.
13. Provide migrations and seed scripts.
14. Use realistic demo data only in a separate development environment.
15. Do not silently ignore errors.
16. Do not claim features are complete unless they have been implemented and tested.
17. Avoid unnecessary microservices. Prefer a well-structured modular monolith.
18. Maintain clean, reusable, documented code.

## 24. DEPLOYMENT AND HANDOVER

Prepare the application for production deployment.

Deliver:

- Complete frontend source
- Complete backend source
- PostgreSQL schema and migrations
- Dockerfiles
- Docker Compose configuration
- .env.example
- Seed data scripts
- Automated test suite
- API documentation
- Deployment instructions
- Database backup instructions
- Admin account setup instructions
- User manual

Include a README with exact commands for local development and production deployment.

Provide a deployment checklist covering:
- Database configuration
- Environment variables
- HTTPS
- Domain setup
- CORS
- Database migrations
- Admin creation
- Backup verification
- Monitoring
- Security checks

Do not expose default administrator credentials in production. Provide a secure first-admin creation procedure.

## 25. FINAL ACCEPTANCE CRITERIA

The application is accepted only when:

- Admin can log in securely.
- Role-based access control works.
- Customers and suppliers can be managed.
- Products and inventory can be managed.
- EV serial numbers can be tracked.
- Sales and purchase workflows function.
- GST invoices calculate correctly.
- Invoice PDFs download successfully.
- Payments and outstanding balances update correctly.
- Stock updates are accurate.
- Financial reports use actual database data.
- Audit trails work.
- Database migrations run successfully.
- Automated critical tests pass.
- The application can be deployed using documented instructions.

**FINAL INSTRUCTION:**

Start by inspecting the existing project files and repository structure.

Then create an implementation roadmap and immediately begin building the application.

Work incrementally, implement complete functional modules, run tests, fix errors, and continue until the acceptance criteria are met.

If you encounter missing business information, create configurable settings instead of inventing values.

Prioritize correctness, financial data integrity, security, usability, and maintainability over visual effects.

Build this as software that a real Indian EV business can operate every day.