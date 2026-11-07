import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { api, apiBlob } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatINR } from "../lib/format";

type InvoiceLine = {
  id: number;
  description: string;
  hsn_sac: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
};
type Invoice = {
  id: number;
  invoice_number: string | null;
  customer_name: string | null;
  invoice_date: string;
  grand_total: string;
  status: string;
  lines: InvoiceLine[];
};
type CreditNoteRow = {
  id: number;
  note_number: string;
  invoice_id: number;
  note_date: string;
  reason: string;
  grand_total: string;
  restock: boolean;
};
type Page<T> = { items: T[]; total: number };

export function CreditNotesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [invoiceId, setInvoiceId] = useState("");
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [noteDate, setNoteDate] = useState(new Date().toISOString().slice(0, 10));
  const [reason, setReason] = useState("");
  const [restock, setRestock] = useState(false);
  const [confirmIssue, setConfirmIssue] = useState(false);
  const [creditNotePage, setCreditNotePage] = useState(1);

  const invoices = useQuery({
    queryKey: ["invoices", "issued-options"],
    queryFn: ({ signal }) => api<Page<Invoice>>("/invoices", { query: { status: "issued", page: 1, page_size: 100 }, signal }),
    retry: false,
  });
  const selectedInvoice = useQuery({
    queryKey: ["invoices", "credit-note-source", invoiceId],
    queryFn: ({ signal }) => api<Invoice>(`/invoices/${invoiceId}`, { signal }),
    enabled: !!invoiceId,
    retry: false,
  });
  const creditNotes = useQuery({
    queryKey: ["credit-notes", "list", creditNotePage],
    queryFn: ({ signal }) => api<Page<CreditNoteRow>>("/credit-notes", { query: { page: creditNotePage, page_size: 20 }, signal }),
    retry: false,
  });
  const create = useMutation({
    mutationFn: () => api<CreditNoteRow>("/credit-notes", {
      method: "POST",
      body: {
        invoice_id: Number(invoiceId),
        note_date: noteDate,
        reason,
        restock,
        lines: Object.entries(quantities)
          .filter(([, quantity]) => Number(quantity) > 0)
          .map(([id, quantity]) => ({ invoice_item_id: Number(id), quantity })),
      },
    }),
    onSuccess: async (note) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["credit-notes"] }),
        queryClient.invalidateQueries({ queryKey: ["invoices"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["customer-ledger"] }),
      ]);
      setInvoiceId("");
      setQuantities({});
      setReason("");
      setRestock(false);
      setCreditNotePage(1);
      toast("success", `Credit note ${note.note_number} issued.`);
    },
    onError: () => setConfirmIssue(false),
  });

  async function download(note: CreditNoteRow) {
    try {
      const blob = await apiBlob(`/credit-notes/${note.id}/pdf`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${note.note_number}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not download credit note PDF.");
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Credit notes</h1>
        <p className="text-sm text-slate-500">Issue partial or full notes against issued invoices; tax and totals are calculated by the server.</p>
      </header>
      {can("sales:create") && (
        <Card className="space-y-4 p-5">
          <h2 className="text-lg font-semibold">Create a credit note</h2>
          {invoices.isLoading && <LoadingState label="Loading issued invoices…" />}
          {invoices.isError && <ErrorState message={invoices.error.message} onRetry={() => void invoices.refetch()} />}
          {invoices.data && (
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="text-sm font-medium text-slate-700 sm:col-span-2">Issued invoice
                <select disabled={create.isPending} value={invoiceId} onChange={(event) => { setInvoiceId(event.target.value); setQuantities({}); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                  <option value="">Select invoice</option>
                  {invoices.data.items.map((invoice) => <option key={invoice.id} value={invoice.id}>{invoice.invoice_number ?? `Invoice ${invoice.id}`} · {invoice.customer_name} · {formatINR(invoice.grand_total)}</option>)}
                </select>
              </label>
              {selectedInvoice.isLoading && <LoadingState label="Loading invoice lines…" />}
              {selectedInvoice.isError && <ErrorState message={selectedInvoice.error.message} onRetry={() => void selectedInvoice.refetch()} />}
              {selectedInvoice.data && (
                <div className="space-y-3 sm:col-span-2">
                  <p className="text-sm text-slate-600">Original invoice {selectedInvoice.data.invoice_number} · {formatDate(selectedInvoice.data.invoice_date)}</p>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[620px] text-left text-sm">
                      <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr><th className="p-2">Line</th><th className="p-2">Qty invoiced</th><th className="p-2">Unit price</th><th className="p-2">Return qty</th></tr></thead>
                      <tbody className="divide-y divide-ivory-200">
                        {selectedInvoice.data.lines.map((line) => (
                          <tr key={line.id}>
                            <td className="p-2">{line.description} <span className="text-slate-500">({line.hsn_sac})</span></td>
                            <td className="p-2">{line.quantity} {line.unit}</td>
                            <td className="p-2">{formatINR(line.unit_price)}</td>
                            <td className="p-2"><input
                              aria-label={`Return quantity for ${line.description}`}
                              type="number" disabled={create.isPending} min="0" max={line.quantity} step="0.001"
                              value={quantities[line.id] ?? ""}
                              onChange={(event) => setQuantities((current) => ({ ...current, [line.id]: event.target.value }))}
                              className="w-32 rounded-lg border border-ivory-300 px-3 py-2"
                            /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <Field label="Credit note date" disabled={create.isPending} type="date" required value={noteDate} onChange={(event) => setNoteDate(event.target.value)} />
              <Field label="Reason (min. 10 characters)" disabled={create.isPending} required minLength={10} value={reason} onChange={(event) => setReason(event.target.value)} />
              <label className="flex items-center gap-2 text-sm sm:col-span-2">
                <input type="checkbox" disabled={create.isPending} checked={restock} onChange={(event) => setRestock(event.target.checked)} />
                Restock eligible returned goods to their original warehouse / serial
              </label>
              <div className="sm:col-span-2">
                <Button
                  loading={create.isPending}
                  disabled={!invoiceId || reason.trim().length < 10 || !Object.values(quantities).some((value) => Number(value) > 0) || !can("sales:create")}
                  onClick={() => setConfirmIssue(true)}
                >Issue credit note</Button>
                {create.isError && <p role="alert" className="mt-2 text-sm text-red-700">{create.error.message}</p>}
              </div>
            </div>
          )}
        </Card>
      )}
      {!can("sales:create") && <p className="rounded-lg bg-gold-100 p-3 text-sm text-slate-700">You have read-only access to credit notes.</p>}

      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-4"><h2 className="font-semibold">Issued credit notes</h2></div>
        {creditNotes.isLoading && <LoadingState />}
        {creditNotes.isError && <ErrorState message={creditNotes.error.message} onRetry={() => void creditNotes.refetch()} />}
        {creditNotes.data?.items.length === 0 && <EmptyState title="No credit notes yet" />}
        {creditNotes.data && creditNotes.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr><th className="p-3">Note</th><th className="p-3">Invoice ID</th><th className="p-3">Date</th><th className="p-3">Reason</th><th className="p-3">Amount</th><th className="p-3">Restock</th><th className="p-3">PDF</th></tr></thead>
                <tbody className="divide-y divide-ivory-200">{creditNotes.data.items.map((note) => (
                  <tr key={note.id}>
                    <td className="p-3 font-medium">{note.note_number}</td><td className="p-3">{note.invoice_id}</td>
                    <td className="p-3">{formatDate(note.note_date)}</td><td className="p-3">{note.reason}</td>
                    <td className="p-3">{formatINR(note.grand_total)}</td><td className="p-3"><Badge>{note.restock ? "restocked" : "not restocked"}</Badge></td>
                    <td className="p-3"><Button variant="secondary" onClick={() => void download(note)}>Download PDF</Button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {creditNotes.data.total > 20 && (
              <div className="flex items-center justify-between border-t border-ivory-200 p-4 text-sm">
                <span className="text-slate-500">Showing {(creditNotePage - 1) * 20 + 1}–{Math.min(creditNotePage * 20, creditNotes.data.total)} of {creditNotes.data.total}</span>
                <div className="flex gap-2">
                  <Button variant="secondary" disabled={creditNotePage === 1 || creditNotes.isFetching} onClick={() => setCreditNotePage((current) => current - 1)}>Previous</Button>
                  <Button variant="secondary" disabled={creditNotePage * 20 >= creditNotes.data.total || creditNotes.isFetching} onClick={() => setCreditNotePage((current) => current + 1)}>Next</Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
      <ConfirmDialog
        open={confirmIssue}
        title={restock ? "Issue credit note and restock" : "Issue credit note"}
        message={restock
          ? "This issues a GST credit note, posts accounting reversals and restores the returned quantity to the original warehouse. Serialized items will be marked available."
          : "This issues a GST credit note and posts accounting reversals. The issued credit note cannot be edited or deleted."}
        confirmLabel={restock ? "Issue and restock" : "Issue credit note"}
        danger
        onCancel={() => { if (!create.isPending) setConfirmIssue(false); }}
        onConfirm={() => { setConfirmIssue(false); create.mutate(); }}
      />
    </div>
  );
}
