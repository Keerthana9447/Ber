import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { api, apiBlob } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatINR } from "../lib/format";

type Page<T> = { items: T[]; total: number };
type PurchaseType = "purchase_request" | "purchase_order" | "purchase_invoice";
type PurchaseItem = {
  id: number;
  source_purchase_item_id: number | null;
  product_id: number;
  description: string | null;
  hsn_sac: string;
  quantity: string;
  unit_price: string;
  discount: string;
  taxable_value: string;
  gst_rate: string;
  cess_rate: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
};
type DebitNote = {
  id: number;
  note_number: string;
  note_date: string;
  reason?: string | null;
  status: string;
  grand_total: string;
};
export type Purchase = {
  id: number;
  number: string;
  doc_type: PurchaseType;
  supplier_id: number | null;
  supplier_name: string | null;
  supplier_invoice_no?: string | null;
  doc_date: string;
  due_date?: string | null;
  status: string;
  grand_total: string;
  taxable_total?: string;
  cgst_total?: string;
  sgst_total?: string;
  igst_total?: string;
  cess_total?: string;
  parent_id: number | null;
  items?: PurchaseItem[];
  debit_notes?: DebitNote[];
  goods_receipts?: { id: number; grn_number: string; received_date: string; status: string }[];
};
type Supplier = { id: number; name: string; code: string; is_active?: boolean };
type Product = {
  id: number;
  name: string;
  sku: string;
  purchase_price: string;
  gst_rate: string;
  cess_rate: string;
  is_serialized: boolean;
};
type Warehouse = { id: number; name: string; is_active?: boolean };
type ProductOptions = { warehouses: Warehouse[] };
type PurchaseAction = "approve" | "convert-to-order" | "receive" | "invoice" | "return" | "cancel" | "pdf";
type ActionTarget = { kind: "purchase"; purchase: Purchase } | { kind: "debit-note"; note: DebitNote; purchase: Purchase };
type FormMode = "request" | "receive" | "invoice" | "return";
type DraftLine = { product_id: string; quantity: string; unit_price: string; discount: string; gst_rate: string; cess_rate: string };
type SerialOption = { id: number; chassis_no: string | null; vin: string | null; motor_no: string | null; battery_serial: string | null; warehouse_id: number };

const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
const emptyDraftLine = (): DraftLine => ({ product_id: "", quantity: "1", unit_price: "", discount: "0", gst_rate: "0", cess_rate: "0" });
const inputClass = "w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm";
const typeLabel: Record<PurchaseType, string> = {
  purchase_request: "Request",
  purchase_order: "Purchase order",
  purchase_invoice: "Supplier invoice",
};

export function purchaseActions(purchase: Purchase, canManage: boolean): PurchaseAction[] {
  if (!canManage) return ["pdf"];
  const actions: PurchaseAction[] = ["pdf"];
  if (purchase.doc_type === "purchase_request" && purchase.status === "draft") actions.unshift("approve");
  if (purchase.doc_type === "purchase_request" && purchase.status === "approved") actions.unshift("convert-to-order");
  if (purchase.doc_type === "purchase_order" && ["ordered", "received"].includes(purchase.status)) {
    actions.unshift("invoice", "receive");
  }
  if (purchase.doc_type === "purchase_invoice" && purchase.status === "posted") actions.unshift("return");
  if (purchase.doc_type === "purchase_invoice" && purchase.status === "posted") actions.push("cancel");
  return actions;
}

export function PurchaseDetailPanel({
  purchase,
  canManage,
  onAction,
  onNoteAction,
  debitNotes = purchase.debit_notes ?? [],
}: {
  purchase: Purchase;
  canManage: boolean;
  onAction: (action: PurchaseAction, purchase: Purchase) => void;
  onNoteAction: (action: "pdf" | "cancel", note: DebitNote, purchase: Purchase) => void;
  debitNotes?: DebitNote[];
}) {
  const actions = purchaseActions(purchase, canManage);
  const actionLabels: Record<PurchaseAction, string> = {
    approve: "Approve request",
    "convert-to-order": "Convert to order",
    receive: "Record GRN",
    invoice: "Record supplier invoice",
    return: "Create purchase return",
    cancel: "Cancel document",
    pdf: "Download PDF",
  };
  return (
    <div className="space-y-5">
      <Card className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-forest-600">{typeLabel[purchase.doc_type]}</p>
            <h2 className="text-xl font-semibold text-forest-900">{purchase.number}</h2>
            <p className="mt-1 text-sm text-slate-500">{purchase.supplier_name ?? "Supplier not assigned"} · {formatDate(purchase.doc_date)}</p>
          </div>
          <Badge>{purchase.status}</Badge>
        </div>
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div><dt className="text-slate-500">Total</dt><dd className="font-semibold">{formatINR(purchase.grand_total)}</dd></div>
          {purchase.due_date && <div><dt className="text-slate-500">Due date</dt><dd>{formatDate(purchase.due_date)}</dd></div>}
          {purchase.supplier_invoice_no && <div><dt className="text-slate-500">Supplier invoice no.</dt><dd>{purchase.supplier_invoice_no}</dd></div>}
        </dl>
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => (
            <Button
              key={action}
              variant={action === "cancel" ? "danger" : "secondary"}
              onClick={() => onAction(action, purchase)}
            >{actionLabels[action]}</Button>
          ))}
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-3"><h3 className="font-semibold">Document lines</h3></div>
        {!purchase.items?.length ? <EmptyState title="No line details available" /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>
                <th className="p-3">Item</th><th className="p-3">HSN/SAC</th><th className="p-3">Quantity</th>
                <th className="p-3">Unit price</th><th className="p-3">Taxable</th><th className="p-3">GST</th>
              </tr></thead>
              <tbody className="divide-y divide-ivory-200">{purchase.items.map((item) => (
                <tr key={item.id}>
                  <td className="p-3">{item.description ?? `Product ${item.product_id}`}</td><td className="p-3">{item.hsn_sac}</td>
                  <td className="p-3">{item.quantity}</td><td className="p-3">{formatINR(item.unit_price)}</td>
                  <td className="p-3">{formatINR(item.taxable_value)}</td><td className="p-3">{item.gst_rate}%</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {purchase.goods_receipts && purchase.goods_receipts.length > 0 && (
        <Card className="p-5">
          <h3 className="mb-3 font-semibold">Goods receipts</h3>
          <ul className="space-y-2 text-sm">{purchase.goods_receipts.map((receipt) => (
            <li key={receipt.id} className="flex justify-between gap-3"><span>{receipt.grn_number} · {formatDate(receipt.received_date)}</span><Badge>{receipt.status}</Badge></li>
          ))}</ul>
        </Card>
      )}
      {debitNotes.length > 0 && (
        <Card className="overflow-hidden">
          <div className="border-b border-ivory-200 px-5 py-3"><h3 className="font-semibold">Purchase returns / debit notes</h3></div>
          <div className="divide-y divide-ivory-200">{debitNotes.map((note) => (
            <div key={note.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
              <div><p className="font-medium">{note.note_number} · {formatDate(note.note_date)}</p><p className="text-slate-500">{note.reason}</p></div>
              <div className="flex items-center gap-3"><Badge>{note.status}</Badge><span>{formatINR(note.grand_total)}</span>
                <Button variant="secondary" onClick={() => onNoteAction("pdf", note, purchase)}>Download PDF</Button>
                {canManage && note.status === "issued" && <Button variant="danger" onClick={() => onNoteAction("cancel", note, purchase)}>Cancel debit note</Button>}
              </div>
            </div>
          ))}</div>
        </Card>
      )}
    </div>
  );
}

export function PurchasingPage() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [docType, setDocType] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showRequest, setShowRequest] = useState(false);
  const [mode, setMode] = useState<FormMode | null>(null);
  const [supplierId, setSupplierId] = useState("");
  const [requestDate, setRequestDate] = useState(today);
  const [requestDueDate, setRequestDueDate] = useState("");
  const [requestLines, setRequestLines] = useState<DraftLine[]>([emptyDraftLine()]);
  const [receivedDate, setReceivedDate] = useState(today);
  const [warehouseId, setWarehouseId] = useState("");
  const [receiptQty, setReceiptQty] = useState<Record<number, string>>({});
  const [receiptSerials, setReceiptSerials] = useState<Record<number, string>>({});
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState(today);
  const [invoiceDueDate, setInvoiceDueDate] = useState("");
  const [invoiceQty, setInvoiceQty] = useState<Record<number, string>>({});
  const [returnDate, setReturnDate] = useState(today);
  const [returnReason, setReturnReason] = useState("");
  const [returnQty, setReturnQty] = useState<Record<number, string>>({});
  const [returnWarehouse, setReturnWarehouse] = useState<Record<number, string>>({});
  const [returnSerials, setReturnSerials] = useState<Record<number, string>>({});
  const [confirmTarget, setConfirmTarget] = useState<ActionTarget | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const canManage = can("purchases:manage");
  const list = useQuery({
    queryKey: ["purchases", "list", page, query, docType],
    queryFn: ({ signal }) => api<Page<Purchase>>("/purchases", { query: { page, page_size: 20, q: query, doc_type: docType }, signal }),
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["purchases", "detail", selectedId],
    queryFn: ({ signal }) => api<Purchase>(`/purchases/${selectedId}`, { signal }),
    enabled: selectedId !== null,
    retry: false,
  });
  const suppliers = useQuery({
    queryKey: ["purchases", "suppliers"],
    queryFn: ({ signal }) => api<Page<Supplier>>("/suppliers", { query: { page: 1, page_size: 100 }, signal }),
    enabled: canManage && showRequest,
    retry: false,
  });
  const products = useQuery({
    queryKey: ["purchases", "products"],
    queryFn: ({ signal }) => api<Page<Product>>("/products", { query: { page: 1, page_size: 100 }, signal }),
    enabled: canManage && (showRequest || mode === "receive" || mode === "invoice" || mode === "return"),
    retry: false,
  });
  const productOptions = useQuery({
    queryKey: ["purchases", "product-options"],
    queryFn: ({ signal }) => api<ProductOptions>("/product-options", { signal }),
    enabled: canManage && (mode === "receive" || mode === "return"),
    retry: false,
  });
  const serials = useQuery({
    queryKey: ["purchases", "serial-options", selectedId, detail.data?.items?.map((item) => item.product_id).join(",")],
    queryFn: async ({ signal }) => {
      const serialized = (detail.data?.items ?? []).filter((item) => products.data?.items.find((product) => product.id === item.product_id)?.is_serialized);
      const values = await Promise.all(serialized.map(async (item) => ({
        productId: item.product_id,
        items: await api<SerialOption[]>(`/products/${item.product_id}/serials`, { signal }),
      })));
      return Object.fromEntries(values.map((value) => [value.productId, value.items])) as Record<number, SerialOption[]>;
    },
    enabled: canManage && mode === "return" && !!detail.data && !!products.data,
    retry: false,
  });
  const productById = useMemo(() => new Map((products.data?.items ?? []).map((product) => [product.id, product])), [products.data]);

  async function refreshPurchases() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["purchases"] }),
      queryClient.invalidateQueries({ queryKey: ["inventory"] }),
      queryClient.invalidateQueries({ queryKey: ["accounting"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    ]);
  }
  const operation = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => api<{ id?: number; number?: string; note_number?: string; status?: string }>(path, { method: "POST", body }),
    onSuccess: async (result, variables) => {
      await refreshPurchases();
      if (result.id && (
        variables.path === "/purchases/requests" ||
        variables.path.endsWith("/convert-to-order") ||
        variables.path.endsWith("/invoices")
      )) setSelectedId(result.id);
      setMode(null);
      setShowRequest(false);
      toast("success", result.number ? `${result.number} saved.` : result.note_number ? `Debit note ${result.note_number} issued.` : "Purchasing action completed.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Purchasing action failed."),
  });

  function setRequestLine(index: number, patch: Partial<DraftLine>) {
    setRequestLines((current) => current.map((line, position) => position === index ? { ...line, ...patch } : line));
  }
  function openForm(action: PurchaseAction) {
    setMode(action === "receive" ? "receive" : action === "invoice" ? "invoice" : action === "return" ? "return" : null);
    if (action === "receive") {
      setReceiptQty({});
      setReceiptSerials({});
    } else if (action === "invoice") {
      setInvoiceQty({});
    } else if (action === "return") {
      setReturnQty({});
      setReturnSerials({});
    }
  }
  async function download(path: string, filename: string) {
    try {
      const blob = await apiBlob(path);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Download failed.");
    }
  }
  function invokeAction(action: PurchaseAction, purchase: Purchase) {
    if (action === "pdf") {
      void download(`/purchases/${purchase.id}/pdf`, `${purchase.number}.pdf`);
    } else if (action === "receive" || action === "invoice" || action === "return") {
      openForm(action);
    } else if (action === "cancel") {
      setCancelReason("");
      setConfirmTarget({ kind: "purchase", purchase });
    } else {
      operation.mutate({ path: `/purchases/${purchase.id}/${action}` });
    }
  }
  function invokeNoteAction(action: "pdf" | "cancel", note: DebitNote, purchase: Purchase) {
    if (action === "pdf") {
      void download(`/debit-notes/${note.id}/pdf`, `${note.note_number}.pdf`);
    } else {
      setCancelReason("");
      setConfirmTarget({ kind: "debit-note", note, purchase });
    }
  }
  function submitRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const lines = requestLines.filter((line) => line.product_id && Number(line.quantity) > 0);
    if (!lines.length) return;
    operation.mutate({
      path: "/purchases/requests",
      body: {
        supplier_id: supplierId ? Number(supplierId) : null,
        doc_date: requestDate,
        due_date: requestDueDate || null,
        lines: lines.map((line) => ({
          product_id: Number(line.product_id),
          quantity: line.quantity,
          unit_price: line.unit_price,
          discount: line.discount || "0",
          gst_rate: line.gst_rate,
          cess_rate: line.cess_rate || "0",
        })),
      },
    });
  }
  function submitWorkflowForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail.data || !mode) return;
    if (mode === "receive") {
      const lines = (detail.data.items ?? []).filter((item) => Number(receiptQty[item.id]) > 0).map((item) => {
        const product = productById.get(item.product_id);
        const serialValues = (receiptSerials[item.id] ?? "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
        return {
          purchase_item_id: item.id,
          quantity: receiptQty[item.id],
          serials: product?.is_serialized ? serialValues.map((chassis_no) => ({ chassis_no })) : [],
        };
      });
      if (!warehouseId || !lines.length) return;
      operation.mutate({ path: `/purchases/${detail.data.id}/goods-receipts`, body: { warehouse_id: Number(warehouseId), received_date: receivedDate, lines } });
    } else if (mode === "invoice") {
      const lines = (detail.data.items ?? []).filter((item) => Number(invoiceQty[item.id]) > 0).map((item) => ({
        purchase_item_id: item.id,
        quantity: invoiceQty[item.id],
        unit_price: item.unit_price,
        discount: item.discount,
        gst_rate: item.gst_rate,
        cess_rate: item.cess_rate,
      }));
      if (!invoiceNumber.trim() || !lines.length) return;
      operation.mutate({
        path: `/purchases/${detail.data.id}/invoices`,
        body: { supplier_invoice_no: invoiceNumber.trim(), doc_date: invoiceDate, due_date: invoiceDueDate || null, lines },
      });
    } else if (mode === "return") {
      const lines = (detail.data.items ?? []).filter((item) => Number(returnQty[item.id]) > 0).map((item) => ({
        purchase_item_id: item.id,
        quantity: returnQty[item.id],
        warehouse_id: Number(returnWarehouse[item.id]),
        serial_ids: (returnSerials[item.id] ?? "").split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0),
      }));
      if (returnReason.trim().length < 10 || !lines.length || lines.some((line) => !line.warehouse_id)) return;
      operation.mutate({ path: `/purchases/${detail.data.id}/debit-notes`, body: { note_date: returnDate, reason: returnReason.trim(), lines } });
    }
  }
  function confirmCancel() {
    if (!confirmTarget) return;
    if (cancelReason.trim().length < 10) {
      toast("error", "Enter a cancellation reason of at least 10 characters.");
      return;
    }
    const target = confirmTarget;
    setConfirmTarget(null);
    if (target.kind === "purchase") {
      operation.mutate({ path: `/purchases/${target.purchase.id}/cancel`, body: { reason: cancelReason.trim() } });
    } else {
      operation.mutate({ path: `/debit-notes/${target.note.id}/cancel`, body: { reason: cancelReason.trim() } });
    }
  }
  function exportReport(report: "purchase-register" | "input-gst") {
    void download(`/reports/${report}.csv`, `${report}.csv`);
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold text-forest-900">Purchases</h1>
          <p className="text-sm text-slate-500">Track supplier requests through orders, goods receipts, invoices and returns.</p></div>
        <div className="flex flex-wrap gap-2">
          {can("reports:read") && <><Button variant="secondary" onClick={() => exportReport("purchase-register")}>Purchase register CSV</Button>
            <Button variant="secondary" onClick={() => exportReport("input-gst")}>Input GST CSV</Button></>}
          {canManage && <Button onClick={() => { setShowRequest((open) => !open); setMode(null); }}>New purchase request</Button>}
        </div>
      </header>

      {showRequest && canManage && (
        <Card className="space-y-4 p-5">
          <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Create a purchase request</h2>
            <Button variant="ghost" onClick={() => setShowRequest(false)}>Close</Button></div>
          {(suppliers.isLoading || products.isLoading) && <LoadingState label="Loading suppliers and products…" />}
          {(suppliers.error || products.error) && <ErrorState message={(suppliers.error ?? products.error)?.message ?? "Purchase setup is unavailable."}
            onRetry={() => { void suppliers.refetch(); void products.refetch(); }} />}
          {suppliers.data && products.data && (
            <form className="space-y-4" onSubmit={submitRequest}>
              <div className="grid gap-4 sm:grid-cols-3">
                <label className="text-sm font-medium text-slate-700">Supplier
                  <select className={`${inputClass} mt-1`} value={supplierId} onChange={(event) => setSupplierId(event.target.value)} required>
                    <option value="">Select supplier</option>{suppliers.data.items.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.code} · {supplier.name}</option>)}
                  </select>
                </label>
                <Field label="Request date" type="date" value={requestDate} onChange={(event) => setRequestDate(event.target.value)} required />
                <Field label="Due date" type="date" value={requestDueDate} onChange={(event) => setRequestDueDate(event.target.value)} />
              </div>
              <div className="space-y-3">
                {requestLines.map((line, index) => (
                  <div key={index} className="grid gap-3 rounded-lg border border-ivory-200 p-3 sm:grid-cols-2 lg:grid-cols-7">
                    <label className="text-sm font-medium text-slate-700 lg:col-span-2">Product
                      <select className={`${inputClass} mt-1`} value={line.product_id} onChange={(event) => {
                        const product = products.data?.items.find((item) => String(item.id) === event.target.value);
                        setRequestLine(index, { product_id: event.target.value, unit_price: product?.purchase_price ?? "", gst_rate: product?.gst_rate ?? "0", cess_rate: product?.cess_rate ?? "0" });
                      }} required>
                        <option value="">Select product</option>{products.data.items.map((product) => <option key={product.id} value={product.id}>{product.sku} · {product.name}</option>)}
                      </select>
                    </label>
                    <Field label="Quantity" type="number" min="0.001" step="0.001" value={line.quantity} onChange={(event) => setRequestLine(index, { quantity: event.target.value })} required />
                    <Field label="Unit price" type="number" min="0" step="0.0001" value={line.unit_price} onChange={(event) => setRequestLine(index, { unit_price: event.target.value })} required />
                    <Field label="GST %" type="number" min="0" max="100" step="0.01" value={line.gst_rate} onChange={(event) => setRequestLine(index, { gst_rate: event.target.value })} required />
                    <Field label="Cess %" type="number" min="0" max="100" step="0.01" value={line.cess_rate} onChange={(event) => setRequestLine(index, { cess_rate: event.target.value })} />
                    <Field label="Discount" type="number" min="0" step="0.01" value={line.discount} onChange={(event) => setRequestLine(index, { discount: event.target.value })} />
                    <div className="sm:col-span-2 lg:col-span-7"><Button type="button" variant="ghost" disabled={requestLines.length === 1} onClick={() => setRequestLines((current) => current.filter((_, position) => position !== index))}>Remove line</Button></div>
                  </div>
                ))}
                <div className="flex gap-2"><Button type="button" variant="secondary" onClick={() => setRequestLines((current) => [...current, emptyDraftLine()])}>Add line</Button>
                    <Button type="submit" loading={operation.isPending} disabled={!supplierId}>Submit request</Button></div>
              </div>
            </form>
          )}
        </Card>
      )}

      {canManage && <p className="text-xs text-slate-500">Requests are GST-calculated by the server; inventory and tax figures are never estimated here.</p>}
      {operation.isError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{operation.error.message}</p>}

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-end gap-3 border-b border-ivory-200 p-4">
          <Field label="Search purchase number" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} className="min-w-[220px] flex-1" />
          <label className="text-sm font-medium text-slate-700">Document
            <select className={`${inputClass} mt-1 min-w-[180px]`} value={docType} onChange={(event) => { setDocType(event.target.value); setPage(1); }}>
              <option value="">All documents</option><option value="purchase_request">Requests</option><option value="purchase_order">Orders</option><option value="purchase_invoice">Supplier invoices</option>
            </select>
          </label>
        </div>
        {list.isLoading && <LoadingState label="Loading purchases…" />}
        {list.isError && <ErrorState message={list.error.message} onRetry={() => void list.refetch()} />}
        {list.data?.items.length === 0 && <EmptyState title="No purchases found" hint="Try another search or start a purchase request." />}
        {list.data && list.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr><th className="p-3">Number</th><th className="p-3">Type</th><th className="p-3">Supplier</th><th className="p-3">Date</th><th className="p-3">Status</th><th className="p-3 text-right">Total</th></tr></thead>
                <tbody className="divide-y divide-ivory-200">{list.data.items.map((purchase) => (
                  <tr key={purchase.id} className={selectedId === purchase.id ? "bg-forest-50" : "hover:bg-ivory-50"}>
                    <td className="p-3"><button className="font-medium text-forest-700 underline-offset-2 hover:underline" onClick={() => { setSelectedId(purchase.id); setMode(null); }}>{purchase.number}</button></td>
                    <td className="p-3">{typeLabel[purchase.doc_type]}</td><td className="p-3">{purchase.supplier_name ?? "—"}</td>
                    <td className="p-3">{formatDate(purchase.doc_date)}</td><td className="p-3"><Badge>{purchase.status}</Badge></td>
                    <td className="p-3 text-right">{formatINR(purchase.grand_total)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {list.data.total > 20 && <div className="flex items-center justify-between border-t border-ivory-200 p-4 text-sm">
              <span className="text-slate-500">Showing {(page - 1) * 20 + 1}–{Math.min(page * 20, list.data.total)} of {list.data.total}</span>
              <div className="flex gap-2"><Button variant="secondary" disabled={page === 1 || list.isFetching} onClick={() => setPage((current) => current - 1)}>Previous</Button>
                <Button variant="secondary" disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => setPage((current) => current + 1)}>Next</Button></div>
            </div>}
          </>
        )}
      </Card>

      {selectedId !== null && <section aria-label="Purchase details" className="space-y-4">
        {detail.isLoading && <LoadingState label="Loading purchase detail…" />}
        {detail.isError && <ErrorState message={detail.error.message} onRetry={() => void detail.refetch()} />}
        {detail.data && <PurchaseDetailPanel purchase={detail.data} canManage={canManage} onAction={invokeAction} onNoteAction={invokeNoteAction} />}
      </section>}

      {mode && detail.data && canManage && (
        <Card className="space-y-4 p-5">
          <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">
            {mode === "receive" ? "Record goods receipt (GRN)" : mode === "invoice" ? "Record supplier invoice" : "Create purchase return"}
          </h2><Button variant="ghost" onClick={() => setMode(null)}>Close</Button></div>
          {(products.isLoading || ((mode === "receive" || mode === "return") && productOptions.isLoading)) && <LoadingState label="Loading purchasing options…" />}
          {(products.error || ((mode === "receive" || mode === "return") && productOptions.error)) &&
            <ErrorState message={(products.error ?? productOptions.error)?.message ?? "Purchasing options are unavailable."}
              onRetry={() => { void products.refetch(); void productOptions.refetch(); }} />}
          {products.data && ((mode !== "receive" && mode !== "return") || productOptions.data) && (
            <form className="space-y-4" onSubmit={submitWorkflowForm}>
              {mode === "receive" && <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Received date" type="date" value={receivedDate} onChange={(event) => setReceivedDate(event.target.value)} required />
                  <label className="text-sm font-medium text-slate-700">Warehouse
                    <select className={`${inputClass} mt-1`} value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)} required>
                      <option value="">Select warehouse</option>{(productOptions.data?.warehouses ?? []).filter((warehouse) => warehouse.is_active !== false).map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
                    </select>
                  </label>
                </div>
                {(detail.data.items ?? []).map((item) => {
                  const product = productById.get(item.product_id);
                  return <div key={item.id} className="grid gap-3 rounded-lg border border-ivory-200 p-3 sm:grid-cols-2">
                    <p className="self-center text-sm font-medium">{item.description ?? `Product ${item.product_id}`} · ordered {item.quantity}</p>
                    <Field label="Quantity received" type="number" min="0" max={item.quantity} step="0.001" value={receiptQty[item.id] ?? ""} onChange={(event) => setReceiptQty((current) => ({ ...current, [item.id]: event.target.value }))} />
                    {product?.is_serialized && <label className="text-sm font-medium text-slate-700 sm:col-span-2">Chassis/VIN/motor/battery serials (one identifier per line)
                      <textarea className={`${inputClass} mt-1`} rows={3} value={receiptSerials[item.id] ?? ""} onChange={(event) => setReceiptSerials((current) => ({ ...current, [item.id]: event.target.value }))} />
                    </label>}
                  </div>;
                })}
              </>}
              {mode === "invoice" && <>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label="Supplier invoice number" value={invoiceNumber} onChange={(event) => setInvoiceNumber(event.target.value)} required />
                  <Field label="Invoice date" type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} required />
                  <Field label="Due date" type="date" value={invoiceDueDate} onChange={(event) => setInvoiceDueDate(event.target.value)} />
                </div>
                <p className="text-sm text-slate-500">Enter only quantities already received. The server validates available GRNs and computes invoice tax.</p>
                {(detail.data.items ?? []).map((item) => <div key={item.id} className="grid items-center gap-3 rounded-lg border border-ivory-200 p-3 sm:grid-cols-2">
                  <p className="text-sm font-medium">{item.description ?? `Product ${item.product_id}`} · order qty {item.quantity} · {formatINR(item.unit_price)} each</p>
                  <Field label="Received quantity to invoice" type="number" min="0" max={item.quantity} step="0.001" value={invoiceQty[item.id] ?? ""} onChange={(event) => setInvoiceQty((current) => ({ ...current, [item.id]: event.target.value }))} />
                </div>)}
              </>}
              {mode === "return" && <>
                {serials.isError && <ErrorState message={serials.error.message} onRetry={() => void serials.refetch()} />}
                <Field label="Return date" type="date" value={returnDate} onChange={(event) => setReturnDate(event.target.value)} required />
                <Field label="Reason (at least 10 characters)" value={returnReason} onChange={(event) => setReturnReason(event.target.value)} minLength={10} required />
                <p className="text-sm text-slate-500">Choose quantities, warehouse and available serial IDs. Stock availability is checked by the server.</p>
                {(detail.data.items ?? []).map((item) => {
                  const product = productById.get(item.product_id);
                  const available = (serials.data?.[item.product_id] ?? []).filter((serial) => String(serial.warehouse_id) === returnWarehouse[item.id]);
                  return <div key={item.id} className="grid gap-3 rounded-lg border border-ivory-200 p-3 sm:grid-cols-2">
                    <p className="self-center text-sm font-medium">{item.description ?? `Product ${item.product_id}`} · billed {item.quantity}</p>
                    <Field label="Return quantity" type="number" min="0" max={item.quantity} step="0.001" value={returnQty[item.id] ?? ""} onChange={(event) => setReturnQty((current) => ({ ...current, [item.id]: event.target.value }))} />
                    <label className="text-sm font-medium text-slate-700">Return warehouse
                      <select className={`${inputClass} mt-1`} value={returnWarehouse[item.id] ?? ""} onChange={(event) => setReturnWarehouse((current) => ({ ...current, [item.id]: event.target.value }))}>
                        <option value="">Select warehouse</option>{(productOptions.data?.warehouses ?? []).filter((warehouse) => warehouse.is_active !== false).map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
                      </select>
                    </label>
                    {product?.is_serialized && <label className="text-sm font-medium text-slate-700">Available serials
                      <select className={`${inputClass} mt-1`} multiple value={(returnSerials[item.id] ?? "").split(",").filter(Boolean)} onChange={(event) => setReturnSerials((current) => ({ ...current, [item.id]: Array.from(event.target.selectedOptions).map((option) => option.value).join(",") }))}>
                        {available.map((serial) => <option key={serial.id} value={serial.id}>{serial.id} · {serial.chassis_no ?? serial.vin ?? serial.motor_no ?? serial.battery_serial}</option>)}
                      </select>
                    </label>}
                  </div>;
                })}
              </>}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" loading={operation.isPending} disabled={mode === "return" && returnReason.trim().length < 10}>
                  {mode === "receive" ? "Post goods receipt" : mode === "invoice" ? "Post supplier invoice" : "Issue debit note"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => setMode(null)}>Cancel</Button>
              </div>
            </form>
          )}
        </Card>
      )}
      <ConfirmDialog
        open={confirmTarget !== null}
        title={confirmTarget?.kind === "debit-note" ? "Cancel debit note?" : "Cancel purchase document?"}
        message="This action is recorded by the backend and may reverse posted inventory or accounting entries. Continue only if this document should be cancelled."
        confirmLabel="Confirm cancellation"
        danger
        onCancel={() => { setConfirmTarget(null); setCancelReason(""); }}
        onConfirm={confirmCancel}
      >
        <Field
          label="Cancellation reason (at least 10 characters)"
          value={cancelReason}
          onChange={(event) => setCancelReason(event.target.value)}
          minLength={10}
          required
        />
      </ConfirmDialog>
    </div>
  );
}
