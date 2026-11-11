import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { api, isEndpointMissing } from "../lib/api";
import { formatCount, formatDate, formatINR } from "../lib/format";
import type { Column, ModuleDef } from "../lib/modules";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, NotAvailableState } from "../components/ui";

type Row = Record<string, unknown>;
const PAGE_SIZE = 20;

// Accepts {items,total} (preferred) or a bare array (paged here in the browser).
function normalize(data: unknown, page: number): { rows: Row[]; total: number } {
  if (Array.isArray(data)) return { rows: data.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) as Row[], total: data.length };
  if (data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items)) {
    const d = data as { items: Row[]; total?: number };
    return { rows: d.items, total: Number(d.total ?? d.items.length) };
  }
  throw new Error("The server returned an unexpected response for this list.");
}

function Cell({ column, row }: { column: Column; row: Row }) {
  const value = row[column.key];
  if (value === null || value === undefined || value === "") return <span className="text-slate-400">—</span>;
  switch (column.kind) {
    case "money":
      return <span className="tabular-nums">{formatINR(value as string | number)}</span>;
    case "count":
      return <span className="tabular-nums">{formatCount(value as string | number)}</span>;
    case "date":
      return <>{formatDate(String(value))}</>;
    case "badge":
      return <Badge>{String(value)}</Badge>;
    case "bool":
      return <Badge>{value ? "active" : "inactive"}</Badge>;
    default:
      return <>{String(value)}</>;
  }
}

export function ModuleListPage({ module: m }: { module: ModuleDef }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);
  useEffect(() => {
    setPage(1);
    setSearch("");
  }, [m.key]);

  const query = useQuery({
    queryKey: [m.key, "list", page, q],
    queryFn: async ({ signal }) => normalize(await api(m.endpoint, { query: { page, page_size: PAGE_SIZE, q }, signal }), page),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">{m.label}</h1>
          <p className="text-sm text-slate-500">{m.description}</p>
        </div>
        <label className="relative block w-full sm:w-72">
          <span className="sr-only">Search {m.label}</span>
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={`Search ${m.label.toLowerCase()}…`}
            className="w-full rounded-lg border border-ivory-300 bg-white py-2 pl-9 pr-3 text-sm"
          />
        </label>
      </div>

      <Card className="overflow-hidden">
        {query.isLoading && <LoadingState />}
        {query.isError &&
          (isEndpointMissing(query.error) ? (
            <NotAvailableState endpoint={m.endpoint} label={m.label} />
          ) : (
            <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
          ))}
        {query.isSuccess && query.data.rows.length === 0 && (
          <EmptyState title={q ? "No results" : `No ${m.label.toLowerCase()} yet`} hint={q ? "Try a different search." : undefined} />
        )}
        {query.isSuccess && query.data.rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {m.columns.map((c) => (
                    <th key={c.key} scope="col" className="px-4 py-3 font-medium">
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {query.data.rows.map((row, i) => (
                  <tr key={String(row.id ?? i)} className="hover:bg-ivory-50">
                    {m.columns.map((c) => (
                      <td key={c.key} className="px-4 py-3">
                        <Cell column={c} row={row} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {query.isSuccess && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>
              Page {page} of {pages} · {formatCount(total)} records
            </span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label="Next page">
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
