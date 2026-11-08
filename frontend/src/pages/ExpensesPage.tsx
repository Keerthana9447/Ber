import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiBlob, apiUpload } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatINR } from "../lib/format";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";

type ExpenseCategory = { id: number; name: string; account_code: string | null };
type PaymentMethod = { id: number; name: string };
type Expense = {
  id: number;
  expense_number: string;
  expense_date: string;
  category_name: string | null;
  description: string | null;
  amount: string;
  gst_amount: string;
  total_amount: string;
  vendor: string | null;
  method: string | null;
  status: string;
  has_receipt: boolean;
};
type Page<T> = { items: T[]; total: number };

const INPUT_CLASS = "w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm";
const TODAY = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 10);

export function ExpensesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [expenseDate, setExpenseDate] = useState(TODAY);
  const [categoryId, setCategoryId] = useState("");
  const [methodId, setMethodId] = useState("");
  const [amount, setAmount] = useState("");
  const [cgst, setCgst] = useState("0");
  const [sgst, setSgst] = useState("0");
  const [igst, setIgst] = useState("0");
  const [cess, setCess] = useState("0");
  const [vendor, setVendor] = useState("");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [page, setPage] = useState(1);
  const canManage = can("expenses:manage");
  const canPost = can("accounting:post");

  const categories = useQuery({
    queryKey: ["expense-categories"],
    queryFn: ({ signal }) => api<ExpenseCategory[]>("/expense-categories", { signal }),
    retry: false,
  });
  const methods = useQuery({
    queryKey: ["payment-methods"],
    queryFn: ({ signal }) => api<PaymentMethod[]>("/payment-methods", { signal }),
    retry: false,
  });
  const expenses = useQuery({
    queryKey: ["expenses", page],
    queryFn: ({ signal }) => api<Page<Expense>>("/expenses", { query: { page, page_size: 20 }, signal }),
    retry: false,
  });

  async function refreshExpenses() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["expenses"] }),
      queryClient.invalidateQueries({ queryKey: ["accounting"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    ]);
  }
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Expense>("/expenses", { method: "POST", body }),
    onSuccess: async (expense) => {
      await refreshExpenses();
      setPage(1);
      setAmount("");
      setVendor("");
      setReference("");
      setDescription("");
      setCgst("0");
      setSgst("0");
      setIgst("0");
      setCess("0");
      toast("success", `Expense ${expense.expense_number} submitted for approval.`);
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not create expense."),
  });
  const transition = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "post" }) =>
      api(`/expenses/${id}/${action}`, { method: "POST" }),
    onSuccess: async (_, variables) => {
      await refreshExpenses();
      toast("success", variables.action === "post" ? "Expense journal posted." : "Expense approved.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not update expense."),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!categoryId || !methodId) {
      toast("error", "Select an expense category and payment method.");
      return;
    }
    create.mutate({
      expense_date: expenseDate,
      category_id: Number(categoryId),
      method_id: Number(methodId),
      amount,
      cgst_amount: cgst || "0",
      sgst_amount: sgst || "0",
      igst_amount: igst || "0",
      cess_amount: cess || "0",
      vendor: vendor || null,
      reference_no: reference || null,
      description: description || null,
    });
  }

  async function attachReceipt(expense: Expense, file?: File) {
    if (!file) return;
    try {
      await apiUpload(`/expenses/${expense.id}/receipt`, file);
      await refreshExpenses();
      toast("success", "Receipt uploaded.");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not upload receipt.");
    }
  }

  async function downloadReceipt(expense: Expense) {
    try {
      const blob = await apiBlob(`/expenses/${expense.id}/receipt`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${expense.expense_number}-receipt`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not download receipt.");
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Expenses</h1>
        <p className="text-sm text-slate-500">Submit receipts, approve expenses, and post balanced journals.</p>
      </header>

      {canManage && (
        <Card className="p-5">
          <h2 className="mb-4 text-lg font-semibold">Submit an expense</h2>
          {(categories.isLoading || methods.isLoading) && <LoadingState label="Loading expense setup…" />}
          {(categories.error || methods.error) && (
            <ErrorState
              message={(categories.error ?? methods.error)?.message ?? "Expense setup is unavailable."}
              onRetry={() => { void categories.refetch(); void methods.refetch(); }}
            />
          )}
          {categories.data && methods.data && (
            <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" onSubmit={submit}>
              <Field label="Expense date" type="date" value={expenseDate} onChange={(event) => setExpenseDate(event.target.value)} required />
              <label className="block text-sm font-medium text-slate-700">
                Category
                <select className={`${INPUT_CLASS} mt-1`} value={categoryId} onChange={(event) => setCategoryId(event.target.value)} required>
                  <option value="">Select category</option>
                  {categories.data.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                Payment method
                <select className={`${INPUT_CLASS} mt-1`} value={methodId} onChange={(event) => setMethodId(event.target.value)} required>
                  <option value="">Select method</option>
                  {methods.data.map((method) => <option key={method.id} value={method.id}>{method.name}</option>)}
                </select>
              </label>
              <Field label="Taxable amount" type="number" min="0.01" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} required />
              <Field label="Vendor" value={vendor} onChange={(event) => setVendor(event.target.value)} />
              <Field label="Reference number" value={reference} onChange={(event) => setReference(event.target.value)} />
              <Field label="Input CGST" type="number" min="0" step="0.01" value={cgst} onChange={(event) => setCgst(event.target.value)} />
              <Field label="Input SGST" type="number" min="0" step="0.01" value={sgst} onChange={(event) => setSgst(event.target.value)} />
              <Field label="Input IGST" type="number" min="0" step="0.01" value={igst} onChange={(event) => setIgst(event.target.value)} />
              <Field label="Input cess" type="number" min="0" step="0.01" value={cess} onChange={(event) => setCess(event.target.value)} />
              <label className="block text-sm font-medium text-slate-700 sm:col-span-2 lg:col-span-3">
                Description
                <textarea className={`${INPUT_CLASS} mt-1`} maxLength={2000} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
              </label>
              <div className="sm:col-span-2 lg:col-span-3">
                <Button type="submit" loading={create.isPending}>Submit for approval</Button>
              </div>
            </form>
          )}
        </Card>
      )}

      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-4">
          <h2 className="font-semibold">Expense history</h2>
        </div>
        {expenses.isLoading && <LoadingState />}
        {expenses.isError && <ErrorState message={expenses.error.message} onRetry={() => void expenses.refetch()} />}
        {expenses.data?.items.length === 0 && <EmptyState title="No expenses yet" />}
        {expenses.data && expenses.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase text-slate-500">
                <tr>
                  {["Number / date", "Category / vendor", "Amount", "Payment", "Status", "Receipt", "Actions"].map((heading) => (
                    <th key={heading} scope="col" className="px-4 py-3 font-medium">{heading}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {expenses.data.items.map((expense) => (
                  <tr key={expense.id}>
                    <td className="px-4 py-3"><strong>{expense.expense_number}</strong><div className="text-xs text-slate-500">{formatDate(expense.expense_date)}</div></td>
                    <td className="px-4 py-3">{expense.category_name ?? "—"}<div className="text-xs text-slate-500">{expense.vendor ?? expense.description ?? "—"}</div></td>
                    <td className="px-4 py-3 tabular-nums">{formatINR(expense.total_amount)}<div className="text-xs text-slate-500">GST {formatINR(expense.gst_amount)}</div></td>
                    <td className="px-4 py-3">{expense.method ?? "—"}</td>
                    <td className="px-4 py-3"><Badge>{expense.status}</Badge></td>
                    <td className="px-4 py-3">
                      {expense.has_receipt ? (
                        <Button variant="secondary" onClick={() => void downloadReceipt(expense)}>Download</Button>
                      ) : canManage && ["pending", "approved"].includes(expense.status) ? (
                        <label className="inline-flex cursor-pointer rounded-lg border border-ivory-300 px-3 py-2 text-xs font-medium text-forest-800 hover:bg-ivory-100">
                          Attach
                          <input
                            type="file"
                            accept="application/pdf,image/jpeg,image/png,image/webp"
                            className="sr-only"
                            aria-label={`Attach receipt for ${expense.expense_number}`}
                            onChange={(event) => { void attachReceipt(expense, event.target.files?.[0]); event.currentTarget.value = ""; }}
                          />
                        </label>
                      ) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        {canManage && expense.status === "pending" && (
                          <Button variant="secondary" disabled={transition.isPending} onClick={() => transition.mutate({ id: expense.id, action: "approve" })}>Approve</Button>
                        )}
                        {canPost && expense.status === "approved" && (
                          <Button disabled={transition.isPending} onClick={() => transition.mutate({ id: expense.id, action: "post" })}>Post</Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
