import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity } from "lucide-react";
import { api, isEndpointMissing } from "../lib/api";
import { RANGE_LABELS, resolveRange, type RangeKey } from "../lib/dateRanges";
import { formatCount, formatDate, formatINR } from "../lib/format";
import { Card, EmptyState, ErrorState, LoadingState, NotAvailableState, Skeleton } from "../components/ui";

// Expected response of GET /api/v1/dashboard (see docs/API_CONTRACT.md). Money values are decimal strings.
type Num = string | number;
type DashboardData = {
  kpis?: Record<string, Num | null>;
  sales_trend?: { date: string; amount: Num }[];
  purchase_trend?: { date: string; amount: Num }[];
  revenue_vs_expenses?: { period: string; revenue: Num; expenses: Num }[];
  top_products?: { name: string; quantity: Num; amount: Num }[];
  payment_methods?: { method: string; amount: Num }[];
  recent_invoices?: { invoice_number: string; customer_name: string; invoice_date: string; grand_total: Num; status: string }[];
  recent_transactions?: { reference: string; date: string; description: string; amount: Num }[];
};

const KPIS: { key: string; label: string; kind: "money" | "count" }[] = [
  { key: "sales_today", label: "Sales today", kind: "money" },
  { key: "sales_month", label: "Monthly sales", kind: "money" },
  { key: "purchases_total", label: "Total purchases", kind: "money" },
  { key: "revenue_total", label: "Total revenue", kind: "money" },
  { key: "expenses_total", label: "Total expenses", kind: "money" },
  { key: "gross_profit", label: "Gross profit", kind: "money" },
  { key: "receivables_outstanding", label: "Receivables outstanding", kind: "money" },
  { key: "payables_outstanding", label: "Payables outstanding", kind: "money" },
  { key: "customers_total", label: "Customers", kind: "count" },
  { key: "suppliers_total", label: "Suppliers", kind: "count" },
  { key: "products_total", label: "Products", kind: "count" },
  { key: "low_stock_products", label: "Low-stock products", kind: "count" },
  { key: "ev_vehicles_in_stock", label: "EVs in stock", kind: "count" },
];

const COLORS = ["#18563a", "#c9a43c", "#4f9f71", "#a9862c", "#7dbc96", "#14442f"];
// Chart axes need numbers; these conversions are for drawing only, never for accounting.
const n = (v: Num | null | undefined) => Number(v ?? 0);
const compactINR = (v: number) => new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 }).format(v);

function ChartCard({ title, empty, children }: { title: string; empty: boolean; children: ReactNode }) {
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-800">{title}</h3>
      {empty ? <EmptyState title="No data in this period" /> : <div className="h-64">{children}</div>}
    </Card>
  );
}

function SystemStatus() {
  const health = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => api<{ status: string }>("/health", { signal }), retry: false });
  const ok = health.data?.status === "ok";
  return (
    <Card className="flex items-center gap-3 p-4">
      <Activity className={ok ? "h-5 w-5 text-forest-600" : "h-5 w-5 text-slate-400"} aria-hidden />
      <div className="text-sm">
        <p className="font-medium text-slate-800">API status</p>
        <p className="text-slate-500">{health.isLoading ? "Checking…" : ok ? "Backend reachable" : "Backend not reachable"}</p>
      </div>
    </Card>
  );
}

export function DashboardPage() {
  const [rangeKey, setRangeKey] = useState<RangeKey>("this_month");
  const [custom, setCustom] = useState(() => resolveRange("this_month"));
  const range = useMemo(() => resolveRange(rangeKey, new Date(), 4, custom), [rangeKey, custom]);
  const customInvalid = rangeKey === "custom" && custom.from > custom.to;

  const query = useQuery({
    queryKey: ["dashboard", range.from, range.to],
    queryFn: ({ signal }) => api<DashboardData>("/dashboard", { query: range, signal }),
    enabled: !customInvalid,
    retry: false,
  });

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">Dashboard</h1>
          <p className="text-sm text-slate-500">
            {formatDate(range.from)} – {formatDate(range.to)}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-slate-600">Period</span>
            <select
              value={rangeKey}
              onChange={(e) => setRangeKey(e.target.value as RangeKey)}
              className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"
            >
              {(Object.keys(RANGE_LABELS) as RangeKey[]).map((k) => (
                <option key={k} value={k}>
                  {RANGE_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
          {rangeKey === "custom" && (
            <>
              <label className="text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-600">From</span>
                <input type="date" value={custom.from} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
              </label>
              <label className="text-sm">
                <span className="mb-1 block text-xs font-medium text-slate-600">To</span>
                <input type="date" value={custom.to} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
              </label>
            </>
          )}
        </div>
      </div>
      {customInvalid && <p role="alert" className="text-sm text-red-700">The start date must be on or before the end date.</p>}

      <SystemStatus />

      {query.isLoading && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      )}
      {query.isError &&
        (isEndpointMissing(query.error) ? (
          <Card>
            <NotAvailableState endpoint="/dashboard" label="The dashboard" />
          </Card>
        ) : (
          <Card>
            <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
          </Card>
        ))}

      {query.isSuccess && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {KPIS.map((k) => {
              const value = query.data.kpis?.[k.key];
              return (
                <Card key={k.key} className="p-4">
                  <p className="text-xs font-medium text-slate-500">{k.label}</p>
                  <p className="mt-1 text-xl font-semibold text-forest-900">{k.kind === "money" ? formatINR(value) : formatCount(value)}</p>
                </Card>
              );
            })}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard title="Sales trend" empty={!query.data.sales_trend?.length}>
              <ResponsiveContainer>
                <LineChart data={(query.data.sales_trend ?? []).map((p) => ({ date: p.date, amount: n(p.amount) }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
                  <XAxis dataKey="date" tickFormatter={(d: string) => formatDate(d).slice(0, 6)} fontSize={12} />
                  <YAxis tickFormatter={compactINR} fontSize={12} />
                  <Tooltip formatter={(v: number) => formatINR(v)} labelFormatter={(d: string) => formatDate(d)} />
                  <Line type="monotone" dataKey="amount" name="Sales" stroke="#18563a" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Purchase trend" empty={!query.data.purchase_trend?.length}>
              <ResponsiveContainer>
                <LineChart data={(query.data.purchase_trend ?? []).map((p) => ({ date: p.date, amount: n(p.amount) }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
                  <XAxis dataKey="date" tickFormatter={(d: string) => formatDate(d).slice(0, 6)} fontSize={12} />
                  <YAxis tickFormatter={compactINR} fontSize={12} />
                  <Tooltip formatter={(v: number) => formatINR(v)} labelFormatter={(d: string) => formatDate(d)} />
                  <Line type="monotone" dataKey="amount" name="Purchases" stroke="#c9a43c" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Revenue vs expenses" empty={!query.data.revenue_vs_expenses?.length}>
              <ResponsiveContainer>
                <BarChart data={(query.data.revenue_vs_expenses ?? []).map((p) => ({ period: p.period, revenue: n(p.revenue), expenses: n(p.expenses) }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
                  <XAxis dataKey="period" fontSize={12} />
                  <YAxis tickFormatter={compactINR} fontSize={12} />
                  <Tooltip formatter={(v: number) => formatINR(v)} />
                  <Legend />
                  <Bar dataKey="revenue" name="Revenue" fill="#18563a" />
                  <Bar dataKey="expenses" name="Expenses" fill="#c9a43c" />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Payment methods" empty={!query.data.payment_methods?.length}>
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={(query.data.payment_methods ?? []).map((p) => ({ name: p.method, value: n(p.amount) }))} dataKey="value" nameKey="name" outerRadius={90} label>
                    {(query.data.payment_methods ?? []).map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: number) => formatINR(v)} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-4">
              <h3 className="mb-3 text-sm font-semibold text-slate-800">Top-selling products</h3>
              {query.data.top_products?.length ? (
                <ol className="divide-y divide-ivory-200 text-sm">
                  {query.data.top_products.map((p) => (
                    <li key={p.name} className="flex justify-between py-2">
                      <span>{p.name}</span>
                      <span className="text-slate-500">
                        {formatCount(p.quantity)} sold · {formatINR(p.amount)}
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState title="No sales in this period" />
              )}
            </Card>
            <Card className="p-4">
              <h3 className="mb-3 text-sm font-semibold text-slate-800">Recent invoices</h3>
              {query.data.recent_invoices?.length ? (
                <ul className="divide-y divide-ivory-200 text-sm">
                  {query.data.recent_invoices.map((i) => (
                    <li key={i.invoice_number} className="flex justify-between py-2">
                      <span>
                        <span className="font-medium">{i.invoice_number}</span> <span className="text-slate-500">{i.customer_name}</span>
                      </span>
                      <span>{formatINR(i.grand_total)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No invoices yet" />
              )}
            </Card>
          </div>
          {query.isFetching && <LoadingState label="Refreshing…" />}
        </>
      )}
    </div>
  );
}
