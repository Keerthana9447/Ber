import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { api, apiAllPages, apiBlob } from "../lib/api";
import { formatDate, formatINR } from "../lib/format";

type Page<T> = { items: T[]; total: number };
type Product = { id: number; name: string; sku: string; selling_price: string; is_serialized: boolean; is_service: boolean };
type Customer = { id: number; name: string; state_code: string | null };
type SalesLine = {
  id: number;
  product_id: number;
  description: string | null;
  quantity: string;
  unit_price: string;
  discount: string;
  is_serialized?: boolean;
  is_service?: boolean;
  remaining_quantity?: string;
};
type Quote = {
  id: number;
  number: string;
  customer_id: number;
  customer_name: string;
  quote_date: string;
  valid_until: string | null;
  place_of_supply_code: string;
  status: string;
  grand_total: string;
  notes: string | null;
  lines: SalesLine[];
};
type Order = {
  id: number;
  number: string;
  customer_name: string;
  order_date: string;
  status: string;
  grand_total: string;
  lines: SalesLine[];
};
type Challan = { id: number; number: string; customer_name: string; challan_date: string; status: string };
type ReportRow = { key: string; count: number; grand_total: string };
type SerialOption = { id: number; chassis_no: string | null; vin: string | null; motor_no: string | null; battery_serial: string | null };
type DraftLine = { product_id: string; quantity: string; unit_price: string; discount_pct: string };
type ChallanDraftLine = { sales_order_item_id: number; product_id: number; is_serialized?: boolean; quantity: string; serial_id: string; serials: SerialOption[] };

const today = new Date().toISOString().slice(0, 10);
const emptyLine = (): DraftLine => ({ product_id: "", quantity: "1", unit_price: "", discount_pct: "0" });

export function SalesFlowPage() {
  const { pathname } = useLocation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [customerId, setCustomerId] = useState("");
  const [place, setPlace] = useState("");
  const [quoteDate, setQuoteDate] = useState(today);
  const [validUntil, setValidUntil] = useState("");
  const [quoteLines, setQuoteLines] = useState<DraftLine[]>([emptyLine()]);
  const [editingQuote, setEditingQuote] = useState<number | null>(null);
  const [quoteNotes, setQuoteNotes] = useState("");
  const [challanOrder, setChallanOrder] = useState<Order | null>(null);
  const [challanLines, setChallanLines] = useState<ChallanDraftLine[]>([]);
  const [reportGroup, setReportGroup] = useState("day");
  const [reportFrom, setReportFrom] = useState(today.slice(0, 8) + "01");
  const [reportTo, setReportTo] = useState(today);

  const customers = useQuery({
    queryKey: ["sales-flow", "customers"],
    queryFn: ({ signal }) => apiAllPages<Customer>("/customers", { signal }),
    retry: false,
  });
  const products = useQuery({
    queryKey: ["sales-flow", "products"],
    queryFn: ({ signal }) => apiAllPages<Product>("/products", { signal }),
    retry: false,
  });
  const quotes = useQuery({
    queryKey: ["sales-flow", "quotations"],
    queryFn: ({ signal }) => api<Page<Quote>>("/quotations", { query: { page: 1, page_size: 50 }, signal }),
    retry: false,
    enabled: pathname === "/quotations",
  });
  const orders = useQuery({
    queryKey: ["sales-flow", "orders"],
    queryFn: ({ signal }) => api<Page<Order>>("/orders", { query: { page: 1, page_size: 50 }, signal }),
    retry: false,
    enabled: pathname === "/orders",
  });
  const challans = useQuery({
    queryKey: ["sales-flow", "challans"],
    queryFn: ({ signal }) => api<Page<Challan>>("/delivery-challans", { query: { page: 1, page_size: 50 }, signal }),
    retry: false,
    enabled: pathname === "/delivery-challans",
  });
  const report = useQuery({
    queryKey: ["sales-flow", "report", reportGroup, reportFrom, reportTo],
    queryFn: ({ signal }) => api<{ items: ReportRow[] }>("/reports/sales", {
      query: { from_date: reportFrom, to_date: reportTo, group_by: reportGroup },
      signal,
    }),
    retry: false,
    enabled: pathname === "/reports",
  });
  const rowsQuery = pathname === "/quotations" ? quotes : pathname === "/orders" ? orders : pathname === "/delivery-challans" ? challans : report;

  const productById = useMemo(() => new Map((products.data?.items ?? []).map((product) => [product.id, product])), [products.data]);
  const selectedCustomer = customers.data?.items.find((customer) => String(customer.id) === customerId);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["sales-flow"] }),
      queryClient.invalidateQueries({ queryKey: ["invoices"] }),
    ]);
  }

  const createQuote = useMutation({
    mutationFn: () => api<Quote>(editingQuote ? `/quotations/${editingQuote}` : "/quotations", {
      method: editingQuote ? "PUT" : "POST",
      body: {
        customer_id: Number(customerId),
        quote_date: quoteDate,
        valid_until: validUntil || null,
        place_of_supply_code: place || selectedCustomer?.state_code,
        notes: quoteNotes || null,
        lines: quoteLines.map((line) => ({
          product_id: Number(line.product_id),
          quantity: line.quantity,
          unit_price: line.unit_price || undefined,
          discount_pct: line.discount_pct || "0",
        })),
      },
    }),
    onSuccess: async (quote) => {
      await refresh();
      toast("success", `Quotation ${quote.number} saved.`);
      resetQuote();
    },
  });
  const action = useMutation({
    mutationFn: ({ path, method = "POST", body }: { path: string; method?: string; body?: unknown }) =>
      api<Record<string, unknown>>(path, { method, body }),
    onSuccess: async () => {
      await refresh();
      toast("success", "Sales workflow action completed.");
    },
  });

  function resetQuote() {
    setEditingQuote(null);
    setCustomerId("");
    setPlace("");
    setQuoteDate(today);
    setValidUntil("");
    setQuoteNotes("");
    setQuoteLines([emptyLine()]);
  }

  function editQuote(quote: Quote) {
    setEditingQuote(quote.id);
    setCustomerId(String(quote.customer_id));
    setPlace(quote.place_of_supply_code);
    setQuoteDate(quote.quote_date);
    setValidUntil(quote.valid_until ?? "");
    setQuoteNotes(quote.notes ?? "");
    setQuoteLines(quote.lines.map((line) => ({
      product_id: String(line.product_id),
      quantity: line.quantity,
      unit_price: line.unit_price,
      discount_pct: Number(line.quantity) * Number(line.unit_price) > 0
        ? (Number(line.discount) / (Number(line.quantity) * Number(line.unit_price)) * 100).toFixed(2)
        : "0",
    })));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function openChallan(order: Order) {
    setChallanOrder(order);
    const initial: ChallanDraftLine[] = await Promise.all(order.lines.map(async (line) => {
      let serials: SerialOption[] = [];
      if (line.is_serialized) {
        serials = await api<SerialOption[]>(`/products/${line.product_id}/serials`);
      }
      return {
        sales_order_item_id: line.id,
        product_id: line.product_id,
        is_serialized: line.is_serialized,
        quantity: line.is_serialized ? "1" : line.remaining_quantity ?? line.quantity,
        serial_id: "",
        serials,
      };
    }));
    setChallanLines(initial);
  }

  const createChallan = useMutation({
    mutationFn: () => {
      if (!challanOrder) throw new Error("Select an order first.");
      return api<{ id: number; number: string }>(`/orders/${challanOrder.id}/delivery-challans`, {
        method: "POST",
        body: {
          lines: challanLines.filter((line) => Number(line.quantity) > 0).map((line) => ({
            sales_order_item_id: line.sales_order_item_id,
            quantity: line.quantity,
            serial_id: line.serial_id ? Number(line.serial_id) : null,
          })),
        },
      });
    },
    onSuccess: async (challan: { id: number; number: string }) => {
      await action.mutateAsync({ path: `/delivery-challans/${challan.id}/dispatch` });
      setChallanOrder(null);
      toast("success", `Delivery challan ${challan.number} dispatched. No stock has been changed.`);
      await refresh();
    },
  });

  async function download(path: string, name: string) {
    try {
      const blob = await apiBlob(path);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${name}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not download PDF.");
    }
  }

  const title = pathname === "/quotations" ? "Quotations" : pathname === "/orders" ? "Sales orders" :
    pathname === "/delivery-challans" ? "Delivery challans" : "Sales reports";
  const loading = customers.isLoading || products.isLoading || rowsQuery.isLoading;
  const error = customers.error ?? products.error ?? rowsQuery.error;

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">{title}</h1>
        <p className="text-sm text-slate-500">
          Quotations and orders do not post stock or accounting. Stock changes only when an invoice is issued.
        </p>
      </header>
      {pathname === "/quotations" && (
        <Card className="space-y-4 p-5">
          <h2 className="text-lg font-semibold">{editingQuote ? "Edit draft quotation" : "Create quotation"}</h2>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="text-sm font-medium">Customer
              <select className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2" value={customerId}
                onChange={(event) => {
                  const id = event.target.value;
                  setCustomerId(id);
                  setPlace(customers.data?.items.find((customer) => String(customer.id) === id)?.state_code ?? "");
                }}>
                <option value="">Select customer</option>
                {(customers.data?.items ?? []).map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
              </select>
            </label>
            <Field label="Place of supply" value={place} onChange={(event) => setPlace(event.target.value.toUpperCase())} maxLength={2} />
            <Field label="Quotation date" type="date" value={quoteDate} onChange={(event) => setQuoteDate(event.target.value)} />
            <Field label="Valid until" type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} />
            <Field label="Notes" value={quoteNotes} onChange={(event) => setQuoteNotes(event.target.value)} />
          </div>
          <div className="space-y-3">
            {quoteLines.map((line, index) => (
              <div key={index} className="grid items-end gap-3 md:grid-cols-4">
                <label className="text-sm font-medium">Product
                  <select className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2" value={line.product_id}
                    onChange={(event) => {
                      const product = productById.get(Number(event.target.value));
                      setQuoteLines((current) => current.map((item, i) => i === index
                        ? { ...item, product_id: event.target.value, unit_price: product?.selling_price ?? "" } : item));
                    }}>
                    <option value="">Select product</option>
                    {(products.data?.items ?? []).map((product) =>
                      <option key={product.id} value={product.id}>{product.sku} · {product.name}</option>)}
                  </select>
                </label>
                <Field label="Quantity" type="number" min="0.001" step="0.001" value={line.quantity}
                  onChange={(event) => setQuoteLines((current) => current.map((item, i) => i === index ? { ...item, quantity: event.target.value } : item))} />
                <Field label="Unit price" type="number" min="0" step="0.01" value={line.unit_price}
                  onChange={(event) => setQuoteLines((current) => current.map((item, i) => i === index ? { ...item, unit_price: event.target.value } : item))} />
                <div className="flex gap-2">
                  <Field label="Discount %" type="number" min="0" max="100" step="0.01" value={line.discount_pct}
                    onChange={(event) => setQuoteLines((current) => current.map((item, i) => i === index ? { ...item, discount_pct: event.target.value } : item))} />
                  <Button type="button" variant="danger" disabled={quoteLines.length <= 1} onClick={() => setQuoteLines((current) => current.filter((_, i) => i !== index))}>Remove</Button>
                </div>
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="secondary" onClick={() => setQuoteLines((current) => [...current, emptyLine()])}>Add line</Button>
              <Button type="button" loading={createQuote.isPending} disabled={!customerId || !place || quoteLines.some((line) => !line.product_id || !line.quantity || !line.unit_price)}
                onClick={() => createQuote.mutate()}>Save draft</Button>
              {editingQuote && <Button type="button" variant="secondary" onClick={resetQuote}>Cancel edit</Button>}
            </div>
          </div>
          {createQuote.error && <p role="alert" className="text-sm text-red-700">{createQuote.error.message}</p>}
        </Card>
      )}

      {pathname === "/reports" && (
        <Card className="flex flex-wrap items-end gap-3 p-5">
          <Field label="From" type="date" value={reportFrom} onChange={(event) => setReportFrom(event.target.value)} />
          <Field label="To" type="date" value={reportTo} onChange={(event) => setReportTo(event.target.value)} />
          <label className="text-sm font-medium">Group by
            <select className="mt-1 block rounded-lg border border-ivory-300 bg-white px-3 py-2" value={reportGroup} onChange={(event) => setReportGroup(event.target.value)}>
              {["day", "month", "product", "customer", "salesperson", "gst", "branch"].map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
        </Card>
      )}

      {loading && <Card><LoadingState /></Card>}
      {error && <Card><ErrorState message={error.message} onRetry={() => void rowsQuery.refetch()} /></Card>}
      {action.error && <p role="alert" className="text-sm text-red-700">{action.error.message}</p>}

      {pathname === "/quotations" && quotes.isSuccess && (
        <Card className="overflow-x-auto">
          {quotes.data.items.length === 0 ? <EmptyState title="No quotations yet" /> : (
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>{["Number", "Customer", "Date", "Status", "Total", "Actions"].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-ivory-200">{quotes.data.items.map((quote) => (
                <tr key={quote.id}>
                  <td className="px-4 py-3">{quote.number}</td><td className="px-4 py-3">{quote.customer_name}</td>
                  <td className="px-4 py-3">{formatDate(quote.quote_date)}</td><td className="px-4 py-3"><Badge>{quote.status}</Badge></td>
                  <td className="px-4 py-3">{formatINR(quote.grand_total)}</td>
                  <td className="flex flex-wrap gap-2 px-4 py-3">
                    {quote.status === "draft" && <><Button variant="secondary" onClick={() => editQuote(quote)}>Edit</Button>
                      <Button variant="secondary" loading={action.isPending} onClick={() => action.mutate({ path: `/quotations/${quote.id}/send` })}>Mark sent</Button></>}
                    {quote.status === "sent" && <Button loading={action.isPending} onClick={() => action.mutate({ path: `/quotations/${quote.id}/accept` })}>Accept</Button>}
                    {quote.status === "accepted" && <Button loading={action.isPending} onClick={() => action.mutate({ path: `/quotations/${quote.id}/convert-to-order` })}>Create order</Button>}
                    <Button variant="secondary" onClick={() => void download(`/quotations/${quote.id}/pdf`, quote.number)}>PDF</Button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      {pathname === "/orders" && orders.isSuccess && (
        <Card className="overflow-x-auto">
          {orders.data.items.length === 0 ? <EmptyState title="No sales orders yet" /> : (
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>{["Number", "Customer", "Date", "Status", "Total", "Actions"].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-ivory-200">{orders.data.items.map((order) => (
                <tr key={order.id}><td className="px-4 py-3">{order.number}</td><td className="px-4 py-3">{order.customer_name}</td>
                  <td className="px-4 py-3">{formatDate(order.order_date)}</td><td className="px-4 py-3"><Badge>{order.status}</Badge></td><td className="px-4 py-3">{formatINR(order.grand_total)}</td>
                  <td className="flex flex-wrap gap-2 px-4 py-3">
                    {order.status === "draft" && <Button loading={action.isPending} onClick={() => action.mutate({ path: `/orders/${order.id}/confirm` })}>Confirm</Button>}
                    {["confirmed", "partially_delivered"].includes(order.status) && <Button variant="secondary" onClick={() => void openChallan(order)}>Create challan</Button>}
                    {["partially_delivered", "delivered"].includes(order.status) && <Button loading={action.isPending} onClick={() => action.mutate({ path: `/orders/${order.id}/convert-to-invoice` })}>Create invoice draft</Button>}
                    <Button variant="secondary" onClick={() => void download(`/orders/${order.id}/pdf`, order.number)}>PDF</Button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      {pathname === "/delivery-challans" && challans.isSuccess && (
        <Card className="overflow-x-auto">
          {challans.data.items.length === 0 ? <EmptyState title="No delivery challans yet" /> : (
            <table className="w-full text-left text-sm"><thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>{["Number", "Customer", "Date", "Status", "Actions"].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-ivory-200">{challans.data.items.map((challan) => <tr key={challan.id}>
                <td className="px-4 py-3">{challan.number}</td><td className="px-4 py-3">{challan.customer_name}</td><td className="px-4 py-3">{formatDate(challan.challan_date)}</td>
                <td className="px-4 py-3"><Badge>{challan.status}</Badge></td><td className="px-4 py-3">
                  {challan.status === "dispatched" && <Button loading={action.isPending} onClick={() => action.mutate({ path: `/delivery-challans/${challan.id}/deliver` })}>Mark delivered</Button>}
                </td>
              </tr>)}</tbody>
            </table>
          )}
        </Card>
      )}

      {pathname === "/reports" && report.isSuccess && (
        <Card className="overflow-x-auto">
          {report.data.items.length === 0 ? <EmptyState title="No issued sales in this period" /> : (
            <table className="w-full text-left text-sm"><thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr><th className="px-4 py-3">Group</th><th className="px-4 py-3">Documents</th><th className="px-4 py-3">Sales</th></tr></thead>
              <tbody className="divide-y divide-ivory-200">{report.data.items.map((item) => <tr key={item.key}><td className="px-4 py-3">{item.key}</td><td className="px-4 py-3">{item.count}</td><td className="px-4 py-3">{formatINR(item.grand_total)}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
      )}

      {challanOrder && (
        <Card className="space-y-4 border-forest-300 p-5">
          <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Create challan for {challanOrder.number}</h2>
            <Button variant="secondary" onClick={() => setChallanOrder(null)}>Close</Button></div>
          <p className="text-sm text-slate-600">This creates a delivery record only; it does not reserve or reduce stock.</p>
          {challanLines.map((line, index) => <div key={line.sales_order_item_id} className="grid gap-3 md:grid-cols-3">
            <p className="self-end text-sm">{challanOrder.lines.find((item) => item.id === line.sales_order_item_id)?.description ?? `Order line ${line.sales_order_item_id}`}</p>
            <Field label="Delivered quantity" type="number" min="0.001" step="0.001" value={line.quantity}
              onChange={(event) => setChallanLines((current) => current.map((item, i) => i === index ? { ...item, quantity: event.target.value } : item))} />
            {line.is_serialized ? <label className="text-sm font-medium">Available serial
              <select className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2" value={line.serial_id}
                onChange={(event) => setChallanLines((current) => current.map((item, i) => i === index ? { ...item, serial_id: event.target.value } : item))}>
                <option value="">Select serial</option>{line.serials.map((serial) => {
                  const label = [serial.chassis_no, serial.vin, serial.motor_no, serial.battery_serial].filter(Boolean).join(" / ");
                  return <option key={serial.id} value={serial.id}>{label || `Serial ${serial.id}`}</option>;
                })}
              </select>
            </label> : <span />}
          </div>)}
          <Button loading={createChallan.isPending} onClick={() => createChallan.mutate()}>Create and dispatch</Button>
          {createChallan.error && <p role="alert" className="text-sm text-red-700">{createChallan.error.message}</p>}
        </Card>
      )}
    </div>
  );
}
