import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, EmptyState, ErrorState, LoadingState } from "../components/ui";
import { api } from "../lib/api";
import { formatDate, formatINR } from "../lib/format";

type Customer = { id: number; name: string; code: string };
type CustomerPage = { items: Customer[]; total: number };
type LedgerItem = {
  id: number;
  date: string;
  type: string;
  reference: string;
  debit: string;
  credit: string;
  balance: string;
};
type Ledger = {
  customer_id: number;
  customer_name: string;
  opening_balance: string;
  closing_balance: string;
  items: LedgerItem[];
  total: number;
};

export function CustomerLedgerPage() {
  const [customerId, setCustomerId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const customers = useQuery({
    queryKey: ["customers", "ledger-options"],
    queryFn: ({ signal }) => api<CustomerPage>("/customers", { query: { page: 1, page_size: 100 }, signal }),
    retry: false,
  });
  const ledger = useQuery({
    queryKey: ["customer-ledger", customerId, from, to, page],
    queryFn: ({ signal }) => api<Ledger>(`/customers/${customerId}/ledger`, { query: { from: from || undefined, to: to || undefined, page, page_size: 50 }, signal }),
    enabled: !!customerId,
    retry: false,
  });

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Customer ledger</h1>
        <p className="text-sm text-slate-500">Invoices, receipts, credit notes and the running balance from recorded transactions.</p>
      </header>
      <Card className="grid gap-4 p-5 sm:grid-cols-3">
        {customers.isLoading && <LoadingState label="Loading customers…" />}
        {customers.isError && <ErrorState message={customers.error.message} onRetry={() => void customers.refetch()} />}
        {customers.data && (
          <>
            <label className="text-sm font-medium text-slate-700">Customer
              <select value={customerId} onChange={(event) => { setCustomerId(event.target.value); setPage(1); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                <option value="">Select customer</option>
                {customers.data.items.map((customer) => <option key={customer.id} value={customer.id}>{customer.code} · {customer.name}</option>)}
              </select>
            </label>
            <label className="text-sm font-medium text-slate-700">From
              <input type="date" value={from} onChange={(event) => { setFrom(event.target.value); setPage(1); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2" />
            </label>
            <label className="text-sm font-medium text-slate-700">To
              <input type="date" value={to} onChange={(event) => { setTo(event.target.value); setPage(1); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2" />
            </label>
          </>
        )}
      </Card>
      {customerId && ledger.isLoading && <LoadingState label="Loading customer statement…" />}
      {customerId && ledger.isError && <ErrorState message={ledger.error.message} onRetry={() => void ledger.refetch()} />}
      {ledger.data && (
        <Card className="overflow-hidden">
          <div className="flex flex-wrap justify-between gap-2 border-b border-ivory-200 p-5">
            <div><h2 className="font-semibold">{ledger.data.customer_name}</h2><p className="text-sm text-slate-500">Opening balance · {formatINR(ledger.data.opening_balance)}</p></div>
            <div className="text-right"><p className="text-xs uppercase text-slate-500">Closing balance</p><p className="text-lg font-semibold">{formatINR(ledger.data.closing_balance)}</p></div>
          </div>
          {ledger.data.items.length === 0 && <EmptyState title="No ledger entries in this period" />}
          {ledger.data.items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-sm">
                <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr><th className="p-3">Date</th><th className="p-3">Type</th><th className="p-3">Reference</th><th className="p-3 text-right">Debit</th><th className="p-3 text-right">Credit</th><th className="p-3 text-right">Balance</th></tr></thead>
                <tbody className="divide-y divide-ivory-200">{ledger.data.items.map((item, index) => (
                  <tr key={`${item.type}-${item.id}-${index}`}>
                    <td className="p-3">{formatDate(item.date)}</td><td className="p-3 capitalize">{item.type.replace(/_/g, " ")}</td>
                    <td className="p-3">{item.reference}</td><td className="p-3 text-right">{formatINR(item.debit)}</td>
                    <td className="p-3 text-right">{formatINR(item.credit)}</td><td className="p-3 text-right font-medium">{formatINR(item.balance)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {ledger.data.total > 50 && (
            <div className="flex items-center justify-between border-t border-ivory-200 p-4 text-sm">
              <span className="text-slate-500">Showing {(page - 1) * 50 + 1}–{Math.min(page * 50, ledger.data.total)} of {ledger.data.total}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={page === 1 || ledger.isFetching} onClick={() => setPage((current) => current - 1)}>Previous</Button>
                <Button variant="secondary" disabled={page * 50 >= ledger.data.total || ledger.isFetching} onClick={() => setPage((current) => current + 1)}>Next</Button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
