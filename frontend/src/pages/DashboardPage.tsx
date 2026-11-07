import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { api, isEndpointMissing } from "../lib/api";
import { RANGE_LABELS, resolveRange, type RangeKey } from "../lib/dateRanges";
import { formatCount, formatDate, formatINR } from "../lib/format";
import { Card, EmptyState, ErrorState, LoadingState, NotAvailableState, Skeleton } from "../components/ui";
import type { DashboardChartData } from "./DashboardCharts";

const DashboardCharts = lazy(() => import("./DashboardCharts").then((module) => ({ default: module.DashboardCharts })));

// Expected response of GET /api/v1/dashboard (see docs/API_CONTRACT.md). Money values are decimal strings.
type Num = string | number;
type DashboardData = DashboardChartData & {
  kpis?: Record<string, Num | null>;
  top_products?: { name: string; quantity: Num; amount: Num }[];
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

          <Suspense fallback={<Skeleton className="h-64" />}>
            <DashboardCharts data={query.data} />
          </Suspense>

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
