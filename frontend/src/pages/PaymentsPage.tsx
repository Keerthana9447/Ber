import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { api, apiAllPages, apiBlob } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatINR } from "../lib/format";

type Customer = { id: number; name: string };
type Supplier = { id: number; name: string };
type InvoiceOption = { id: number; invoice_number: string | null; customer_id: number; customer_name: string; grand_total: string; status: string };
type SupplierInvoiceOption = { id: number; number: string; grand_total: string; outstanding: string };
type PaymentMethod = { id: number; name: string; ledger_kind: string };
type Allocation = { invoice_id?: number; purchase_id?: number; amount: string };
type PaymentRow = {
  id: number;
  payment_number: string;
  customer_name: string | null;
  supplier_name?: string | null;
  party_name?: string | null;
  customer_id?: number | null;
  direction: string;
  method: string | null;
  amount: string;
  payment_date: string;
  status: string;
  is_advance: boolean;
  allocations: Allocation[];
};
type Page<T> = { items: T[]; total: number };

export function PaymentsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [customerId, setCustomerId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [methodId, setMethodId] = useState("");
  const [amount, setAmount] = useState("");
  const [paymentDate, setPaymentDate] = useState(new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");
  const [invoiceId, setInvoiceId] = useState("");
  const [purchaseId, setPurchaseId] = useState("");
  const [allocationAmount, setAllocationAmount] = useState("");
  const [mode, setMode] = useState<"receipt" | "refund" | "supplier">("receipt");
  const [refundSource, setRefundSource] = useState<"advance" | "invoice_credit">("advance");
  const [reverseTarget, setReverseTarget] = useState<PaymentRow | null>(null);
  const [refundConfirmOpen, setRefundConfirmOpen] = useState(false);
  const [paymentPage, setPaymentPage] = useState(1);

  const customers = useQuery({
    queryKey: ["customers", "payment-options"],
    queryFn: ({ signal }) => apiAllPages<Customer>("/customers", { signal }),
    retry: false,
  });
  const methods = useQuery({
    queryKey: ["payment-methods"],
    queryFn: ({ signal }) => api<PaymentMethod[]>("/payment-methods", { signal }),
    retry: false,
  });
  const suppliers = useQuery({
    queryKey: ["suppliers", "payment-options"],
    queryFn: ({ signal }) => apiAllPages<Supplier>("/suppliers", { signal }),
    enabled: mode === "supplier",
    retry: false,
  });
  const supplierInvoices = useQuery({
    queryKey: ["supplier-outstanding-invoices", supplierId],
    queryFn: ({ signal }) => api<Page<SupplierInvoiceOption>>(`/suppliers/${supplierId}/outstanding-invoices`, { signal }),
    enabled: mode === "supplier" && !!supplierId,
    retry: false,
  });
  const payments = useQuery({
    queryKey: ["payments", "list", paymentPage],
    queryFn: ({ signal }) => api<Page<PaymentRow>>("/payments", { query: { page: paymentPage, page_size: 20 }, signal }),
    retry: false,
  });
  const invoices = useQuery({
    queryKey: ["invoices", "payment-options"],
    queryFn: ({ signal }) => apiAllPages<InvoiceOption>("/invoices", { query: { status: "issued" }, signal }),
    retry: false,
  });

  const record = useMutation({
    mutationFn: () => mode === "supplier"
      ? api<PaymentRow>("/supplier-payments", {
        method: "POST",
        body: {
          supplier_id: Number(supplierId),
          method_id: Number(methodId),
          amount,
          payment_date: paymentDate,
          reference_no: reference || null,
          allocations: [{ purchase_id: Number(purchaseId), amount }],
        },
      })
      : mode === "refund"
      ? api<PaymentRow>("/payments/refunds", {
        method: "POST",
        body: {
          customer_id: Number(customerId),
          method_id: Number(methodId),
          amount,
          payment_date: paymentDate,
          reference_no: reference || null,
          source: refundSource,
          invoice_id: refundSource === "invoice_credit" ? Number(invoiceId) : undefined,
        },
      })
      : api<PaymentRow>("/payments", {
        method: "POST",
        body: {
        customer_id: Number(customerId),
        method_id: Number(methodId),
        amount,
        payment_date: paymentDate,
        reference_no: reference || null,
        allocations: invoiceId ? [{ invoice_id: Number(invoiceId), amount: allocationAmount || amount }] : null,
        },
      }),
    onSuccess: async (payment) => {
      await queryClient.invalidateQueries({ queryKey: ["payments"] });
      await queryClient.invalidateQueries({ queryKey: ["invoices"] });
      await queryClient.invalidateQueries({ queryKey: ["customer-ledger"] });
      await queryClient.invalidateQueries({ queryKey: ["supplier-outstanding-invoices"] });
      await queryClient.invalidateQueries({ queryKey: ["supplier-ledger"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setAmount("");
      setReference("");
      setInvoiceId("");
      setPurchaseId("");
      setAllocationAmount("");
      setRefundConfirmOpen(false);
      setPaymentPage(1);
      toast("success", `${mode === "supplier" ? "Supplier payment" : mode === "refund" ? "Refund" : "Receipt"} ${payment.payment_number} recorded.`);
    },
    onError: () => setRefundConfirmOpen(false),
  });
  const reverse = useMutation({
    mutationFn: (payment: PaymentRow) => api<PaymentRow>(`/payments/${payment.id}/reverse`, { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["payments"] }),
        queryClient.invalidateQueries({ queryKey: ["invoices"] }),
        queryClient.invalidateQueries({ queryKey: ["customer-ledger"] }),
      ]);
      setReverseTarget(null);
      toast("success", "Payment reversed. Its journal reversal is recorded.");
    },
    onError: () => setReverseTarget(null),
  });

  async function download(payment: PaymentRow) {
    try {
      const blob = await apiBlob(`/payments/${payment.id}/pdf`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${payment.payment_number}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not download receipt PDF.");
    }
  }

  const canManage = can("payments:manage");
  const loadError = methods.error ?? (mode === "supplier" ? suppliers.error ?? supplierInvoices.error : customers.error ?? invoices.error);
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Payments</h1>
        <p className="text-sm text-slate-500">Record customer receipts, refunds, and supplier payments with ledger allocation.</p>
      </header>

      <Card className="p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{mode === "supplier" ? "Record a supplier payment" : mode === "refund" ? "Record a customer refund" : "Record a customer receipt"}</h2>
          <div className="flex gap-2">
            <Button type="button" disabled={record.isPending} variant={mode === "receipt" ? "primary" : "secondary"} onClick={() => { setMode("receipt"); setInvoiceId(""); setPurchaseId(""); }}>Receipt</Button>
            <Button type="button" disabled={record.isPending} variant={mode === "refund" ? "primary" : "secondary"} onClick={() => { setMode("refund"); setInvoiceId(""); setPurchaseId(""); }}>Refund</Button>
            <Button type="button" disabled={!canManage || record.isPending} variant={mode === "supplier" ? "primary" : "secondary"} onClick={() => { setMode("supplier"); setInvoiceId(""); setPurchaseId(""); }}>Supplier payment</Button>
          </div>
        </div>
        {(methods.isLoading || (mode === "supplier" ? suppliers.isLoading || (!!supplierId && supplierInvoices.isLoading) : customers.isLoading || invoices.isLoading)) && <LoadingState label="Loading party, invoices and payment methods…" />}
        {loadError && (
          <ErrorState message={loadError.message} onRetry={() => { void customers.refetch(); void methods.refetch(); void invoices.refetch(); void suppliers.refetch(); void supplierInvoices.refetch(); }} />
        )}
        {methods.data && (mode === "supplier" ? suppliers.data : customers.data) && (
          <form
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (mode === "refund") setRefundConfirmOpen(true);
              else record.mutate();
            }}
          >
            {mode === "supplier" ? (
            <label className="text-sm font-medium text-slate-700">Supplier
              <select required disabled={record.isPending} value={supplierId} onChange={(event) => { setSupplierId(event.target.value); setPurchaseId(""); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                <option value="">Select supplier</option>
                {(suppliers.data?.items ?? []).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
              </select>
            </label>
            ) : <label className="text-sm font-medium text-slate-700">Customer
              <select required disabled={record.isPending} value={customerId} onChange={(event) => { setCustomerId(event.target.value); setInvoiceId(""); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                <option value="">Select customer</option>
                {(customers.data?.items ?? []).map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
              </select>
            </label>}
            <label className="text-sm font-medium text-slate-700">Payment method
              <select required disabled={record.isPending} value={methodId} onChange={(event) => setMethodId(event.target.value)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                <option value="">Select method</option>
                {methods.data.map((method) => <option key={method.id} value={method.id}>{method.name} ({method.ledger_kind})</option>)}
              </select>
            </label>
            <Field label="Amount" required disabled={record.isPending} min="0.01" step="0.01" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} />
            <Field label="Transaction date" required disabled={record.isPending} type="date" value={paymentDate} onChange={(event) => setPaymentDate(event.target.value)} />
            <Field label="Reference" disabled={record.isPending} value={reference} onChange={(event) => setReference(event.target.value)} maxLength={255} />
            {mode === "supplier" ? (
              <label className="text-sm font-medium text-slate-700">Outstanding purchase invoice
                <select required disabled={record.isPending || !supplierId} value={purchaseId} onChange={(event) => {
                  setPurchaseId(event.target.value);
                  const selected = supplierInvoices.data?.items.find((item) => item.id === Number(event.target.value));
                  if (selected) setAmount(selected.outstanding);
                }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                  <option value="">Select invoice</option>
                  {(supplierInvoices.data?.items ?? []).map((invoice) => (
                    <option key={invoice.id} value={invoice.id}>{invoice.number} · due {formatINR(invoice.outstanding)}</option>
                  ))}
                </select>
              </label>
            ) : mode === "receipt" ? (
              <>
                <label className="text-sm font-medium text-slate-700">Allocate to invoice (optional; blank allocates oldest-first)
                  <select disabled={record.isPending} value={invoiceId} onChange={(event) => setInvoiceId(event.target.value)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                    <option value="">Oldest-first / advance</option>
                    {(invoices.data?.items ?? []).filter((invoice) => invoice.customer_id === Number(customerId)).map((invoice) => (
                      <option key={invoice.id} value={invoice.id}>{invoice.invoice_number ?? `Invoice ${invoice.id}`} · {formatINR(invoice.grand_total)}</option>
                    ))}
                  </select>
                </label>
                {invoiceId && <Field label="Amount to allocate" disabled={record.isPending} type="number" min="0.01" step="0.01" value={allocationAmount} onChange={(event) => setAllocationAmount(event.target.value)} placeholder={amount} />}
              </>
            ) : (
              <>
                <label className="text-sm font-medium text-slate-700">Refund source
                  <select disabled={record.isPending} value={refundSource} onChange={(event) => { setRefundSource(event.target.value === "invoice_credit" ? "invoice_credit" : "advance"); setInvoiceId(""); }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                    <option value="advance">Unallocated customer advance</option><option value="invoice_credit">Credit from issued invoice</option>
                  </select>
                </label>
                {refundSource === "invoice_credit" && (
                  <label className="text-sm font-medium text-slate-700">Issued invoice credit
                    <select required disabled={record.isPending} value={invoiceId} onChange={(event) => setInvoiceId(event.target.value)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2">
                      <option value="">Select invoice</option>
                      {(invoices.data?.items ?? []).filter((invoice) => invoice.customer_id === Number(customerId)).map((invoice) => (
                        <option key={invoice.id} value={invoice.id}>{invoice.invoice_number ?? `Invoice ${invoice.id}`} · {formatINR(invoice.grand_total)}</option>
                      ))}
                    </select>
                  </label>
                )}
              </>
            )}
            <div className="sm:col-span-2 lg:col-span-3">
              <Button
                type="submit"
                loading={record.isPending}
                disabled={!canManage || (mode === "supplier" ? !supplierId || !purchaseId : !customerId) || !methodId || !amount || !paymentDate || (mode === "receipt" && !!invoiceId && !!allocationAmount && (Number(allocationAmount) <= 0 || Number(allocationAmount) > Number(amount))) || (mode === "refund" && refundSource === "invoice_credit" && !invoiceId)}
              >Record {mode === "supplier" ? "payment" : mode === "refund" ? "refund" : "receipt"}</Button>
              {record.isError && <p role="alert" className="mt-2 text-sm text-red-700">{record.error.message}</p>}
            </div>
          </form>
        )}
      </Card>

      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-4"><h2 className="font-semibold">Payment history</h2></div>
        {payments.isLoading && <LoadingState />}
        {payments.isError && <ErrorState message={payments.error.message} onRetry={() => void payments.refetch()} />}
        {payments.data?.items.length === 0 && <EmptyState title="No payments yet" hint="Receipts, refunds and reversals will appear here." />}
        {payments.data && payments.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-sm">
                <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>
                  <th className="px-4 py-3">Payment</th><th className="px-4 py-3">Direction</th><th className="px-4 py-3">Party</th><th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Method</th><th className="px-4 py-3">Amount</th><th className="px-4 py-3">Allocation</th>
                  <th className="px-4 py-3">Status</th><th className="px-4 py-3">Actions</th>
                </tr></thead>
                <tbody className="divide-y divide-ivory-200">
                  {payments.data.items.map((payment) => (
                    <tr key={payment.id}>
                      <td className="px-4 py-3 font-medium">{payment.payment_number}</td>
                      <td className="px-4 py-3"><Badge>{payment.direction}</Badge></td>
                      <td className="px-4 py-3">{payment.party_name ?? payment.customer_name ?? payment.supplier_name ?? "—"}</td>
                      <td className="px-4 py-3">{formatDate(payment.payment_date)}</td>
                      <td className="px-4 py-3">{payment.method}</td>
                      <td className="px-4 py-3">{formatINR(payment.amount)}</td>
                      <td className="px-4 py-3">{payment.is_advance ? (payment.direction === "out" ? "Advance refund" : "Advance / partly unallocated") : payment.allocations.map((item) => `${item.purchase_id ? "Purchase" : "Invoice"} ${item.purchase_id ?? item.invoice_id}: ${formatINR(item.amount)}`).join(", ")}</td>
                      <td className="px-4 py-3"><Badge>{payment.status}</Badge></td>
                      <td className="px-4 py-3"><div className="flex gap-2">
                        {payment.customer_id && <Button variant="secondary" onClick={() => void download(payment)}>PDF</Button>}
                        {payment.status === "recorded" && canManage && (
                          <Button variant="danger" onClick={() => setReverseTarget(payment)}>Reverse</Button>
                        )}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {payments.data.total > 20 && (
              <div className="flex items-center justify-between border-t border-ivory-200 p-4 text-sm">
                <span className="text-slate-500">Showing {(paymentPage - 1) * 20 + 1}–{Math.min(paymentPage * 20, payments.data.total)} of {payments.data.total}</span>
                <div className="flex gap-2">
                  <Button variant="secondary" disabled={paymentPage === 1 || payments.isFetching} onClick={() => setPaymentPage((current) => current - 1)}>Previous</Button>
                  <Button variant="secondary" disabled={paymentPage * 20 >= payments.data.total || payments.isFetching} onClick={() => setPaymentPage((current) => current + 1)}>Next</Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
      <ConfirmDialog
        open={refundConfirmOpen}
        title="Confirm customer refund"
        message={`Refund ${formatINR(amount || "0")} to ${customers.data?.items.find((customer) => String(customer.id) === customerId)?.name ?? "this customer"} from ${refundSource === "advance" ? "their unallocated advance" : `credit on invoice ${invoiceId || "not selected"}`}. This posts accounting entries and requires a reversal to undo.`}
        confirmLabel="Record refund"
        danger
        onCancel={() => { if (!record.isPending) setRefundConfirmOpen(false); }}
        onConfirm={async () => { try { await record.mutateAsync(); } catch { /* Mutation error is rendered with the form. */ } }}
      />
      <ConfirmDialog
        open={reverseTarget !== null}
        title="Reverse customer payment"
        message={`This posts a reversing journal and restores the corresponding customer balance for ${reverseTarget?.payment_number ?? "this payment"}. The original payment remains in the audit history.`}
        confirmLabel="Reverse payment"
        danger
        onCancel={() => { if (!reverse.isPending) setReverseTarget(null); }}
        onConfirm={async () => { if (reverseTarget) { try { await reverse.mutateAsync(reverseTarget); } catch { /* Mutation error is shown below. */ } } }}
      />
      {reverse.isError && <ErrorState message={reverse.error.message} />}
    </div>
  );
}
