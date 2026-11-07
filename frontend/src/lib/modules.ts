import {
  BarChart3, BookOpen, Boxes, ClipboardList, CreditCard, FileText, LayoutDashboard, Package,
  Settings, ShoppingCart, Store, Truck, UserCog, Users, Wallet,
  type LucideIcon,
} from "lucide-react";

export type ColumnKind = "text" | "money" | "date" | "badge" | "bool" | "count";
export type Column = { key: string; label: string; kind?: ColumnKind };

export type ModuleDef = {
  key: string;
  label: string;
  path: string;
  endpoint: string; // under /api/v1
  icon: LucideIcon;
  group: "Overview" | "Sales" | "Purchasing" | "Inventory" | "Finance" | "Administration";
  permission?: string; // read permission, matches codes seeded by the backend
  description: string;
  columns: Column[];
  special?: "dashboard" | "settings";
};

export const MODULES: ModuleDef[] = [
  { key: "dashboard", label: "Dashboard", path: "/", endpoint: "/dashboard", icon: LayoutDashboard, group: "Overview",
    permission: "dashboard:read",
    description: "Live business metrics.", columns: [], special: "dashboard" },
  { key: "quotations", label: "Quotations", path: "/quotations", endpoint: "/quotations", icon: ClipboardList, group: "Sales",
    permission: "sales:read", description: "Quotations sent to customers.",
    columns: [
      { key: "number", label: "Number" }, { key: "customer_name", label: "Customer" },
      { key: "quote_date", label: "Date", kind: "date" }, { key: "valid_until", label: "Valid until", kind: "date" },
      { key: "status", label: "Status", kind: "badge" }, { key: "grand_total", label: "Total", kind: "money" },
    ] },
  { key: "orders", label: "Sales orders", path: "/orders", endpoint: "/orders", icon: ShoppingCart, group: "Sales",
    permission: "sales:read", description: "Confirmed customer orders.",
    columns: [
      { key: "number", label: "Number" }, { key: "customer_name", label: "Customer" },
      { key: "order_date", label: "Date", kind: "date" }, { key: "status", label: "Status", kind: "badge" },
      { key: "grand_total", label: "Total", kind: "money" },
    ] },
  { key: "invoices", label: "Invoices", path: "/invoices", endpoint: "/invoices", icon: FileText, group: "Sales",
    permission: "sales:read", description: "GST tax invoices, bills of supply and proforma invoices.",
    columns: [
      { key: "invoice_number", label: "Invoice no." }, { key: "customer_name", label: "Customer" },
      { key: "invoice_date", label: "Date", kind: "date" }, { key: "due_date", label: "Due", kind: "date" },
      { key: "status", label: "Status", kind: "badge" }, { key: "payment_status", label: "Payment", kind: "badge" },
      { key: "grand_total", label: "Total", kind: "money" },
    ] },
  { key: "customers", label: "Customers", path: "/customers", endpoint: "/customers", icon: Users, group: "Sales",
    permission: "customers:read", description: "Individuals, businesses, dealers and corporate customers.",
    columns: [
      { key: "code", label: "Code" }, { key: "name", label: "Name" }, { key: "customer_type", label: "Type", kind: "badge" },
      { key: "phone", label: "Phone" }, { key: "city", label: "City" }, { key: "credit_limit", label: "Credit limit", kind: "money" },
    ] },
  { key: "dealers", label: "Dealers", path: "/dealers", endpoint: "/dealers", icon: Store, group: "Sales",
    permission: "dealers:read", description: "Dealer and distributor network.",
    columns: [
      { key: "dealer_code", label: "Code" }, { key: "name", label: "Dealer" }, { key: "territory", label: "Territory" },
      { key: "status", label: "Status", kind: "badge" },
    ] },
  { key: "purchases", label: "Purchases", path: "/purchases", endpoint: "/purchases", icon: Truck, group: "Purchasing",
    permission: "purchases:read", description: "Purchase requests, orders and supplier invoices.",
    columns: [
      { key: "number", label: "Number" }, { key: "doc_type", label: "Type", kind: "badge" },
      { key: "supplier_name", label: "Supplier" }, { key: "doc_date", label: "Date", kind: "date" },
      { key: "status", label: "Status", kind: "badge" }, { key: "grand_total", label: "Total", kind: "money" },
    ] },
  { key: "suppliers", label: "Suppliers", path: "/suppliers", endpoint: "/suppliers", icon: Truck, group: "Purchasing",
    permission: "suppliers:read", description: "Supplier master.",
    columns: [
      { key: "code", label: "Code" }, { key: "name", label: "Name" }, { key: "gstin", label: "GSTIN" },
      { key: "phone", label: "Phone" }, { key: "city", label: "City" },
    ] },
  { key: "products", label: "Products", path: "/products", endpoint: "/products", icon: Package, group: "Inventory",
    permission: "products:read", description: "Vehicles, batteries, spares and services.",
    columns: [
      { key: "sku", label: "SKU" }, { key: "name", label: "Name" }, { key: "brand", label: "Brand" },
      { key: "hsn_sac", label: "HSN/SAC" }, { key: "gst_rate", label: "GST %", kind: "count" },
      { key: "selling_price", label: "Price", kind: "money" }, { key: "is_active", label: "Active", kind: "bool" },
    ] },
  { key: "inventory", label: "Stock", path: "/inventory", endpoint: "/inventory", icon: Boxes, group: "Inventory",
    permission: "inventory:read", description: "Stock by product and warehouse.",
    columns: [
      { key: "sku", label: "SKU" }, { key: "product_name", label: "Product" },
      { key: "warehouse_name", label: "Warehouse" }, { key: "quantity", label: "Quantity", kind: "count" },
    ] },
  { key: "payments", label: "Payments", path: "/payments", endpoint: "/payments", icon: CreditCard, group: "Finance",
    permission: "payments:read", description: "Receipts and supplier payments.",
    columns: [
      { key: "payment_number", label: "Number" }, { key: "customer_name", label: "Customer" },
      { key: "direction", label: "Direction", kind: "badge" }, { key: "payment_date", label: "Date", kind: "date" },
      { key: "method", label: "Method" }, { key: "amount", label: "Amount", kind: "money" },
    ] },
  { key: "credit-notes", label: "Credit notes", path: "/credit-notes", endpoint: "/credit-notes", icon: FileText, group: "Finance",
    permission: "sales:read", description: "Issued GST credit notes and sales returns.",
    columns: [
      { key: "note_number", label: "Credit note" }, { key: "invoice_id", label: "Invoice" },
      { key: "note_date", label: "Date", kind: "date" }, { key: "reason", label: "Reason" },
      { key: "grand_total", label: "Amount", kind: "money" },
    ] },
  { key: "customer-ledger", label: "Customer ledger", path: "/customer-ledger", endpoint: "/customers", icon: BookOpen, group: "Finance",
    permission: "customers:read", description: "Customer invoice, credit-note, receipt and running-balance statement.",
    columns: [] },
  { key: "expenses", label: "Expenses", path: "/expenses", endpoint: "/expenses", icon: Wallet, group: "Finance",
    permission: "expenses:read", description: "Business expenses and approvals.",
    columns: [
      { key: "expense_number", label: "Number" }, { key: "expense_date", label: "Date", kind: "date" },
      { key: "category_name", label: "Category" }, { key: "vendor", label: "Vendor" },
      { key: "status", label: "Status", kind: "badge" }, { key: "amount", label: "Amount", kind: "money" },
    ] },
  { key: "accounting", label: "Accounting", path: "/accounting", endpoint: "/accounting", icon: BookOpen, group: "Finance",
    permission: "accounting:read", description: "Journal entries and ledgers.",
    columns: [
      { key: "reference", label: "Reference" }, { key: "entry_date", label: "Date", kind: "date" },
      { key: "narration", label: "Narration" }, { key: "total_debit", label: "Debit", kind: "money" },
      { key: "total_credit", label: "Credit", kind: "money" },
    ] },
  { key: "reports", label: "Reports", path: "/reports", endpoint: "/reports", icon: BarChart3, group: "Finance",
    permission: "reports:read", description: "Available reports.",
    columns: [{ key: "name", label: "Report" }, { key: "description", label: "Description" }] },
  { key: "users", label: "Users", path: "/users", endpoint: "/users", icon: UserCog, group: "Administration",
    permission: "users:read", description: "User accounts and roles.",
    columns: [
      { key: "full_name", label: "Name" }, { key: "email", label: "Email" }, { key: "role", label: "Role", kind: "badge" },
      { key: "is_active", label: "Active", kind: "bool" }, { key: "last_login_at", label: "Last login", kind: "date" },
    ] },
  { key: "settings", label: "Settings", path: "/settings", endpoint: "/settings", icon: Settings, group: "Administration",
    permission: "settings:read", description: "Company, tax and invoice configuration.", columns: [], special: "settings" },
];

export const GROUP_ORDER: ModuleDef["group"][] = ["Overview", "Sales", "Purchasing", "Inventory", "Finance", "Administration"];

export const moduleByPath = (path: string) => MODULES.find((m) => m.path === path);
