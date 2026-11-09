import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";
import { Pencil, Plus, Search } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingState, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

const PAGE_SIZE = 20;
const dealerStatuses = ["pending", "active", "suspended", "terminated"] as const;
type DealerStatus = (typeof dealerStatuses)[number];
type Dealer = {
  id: number;
  dealer_code: string;
  customer_id: number;
  customer_code: string;
  name: string;
  customer_type: string;
  phone: string | null;
  email: string | null;
  city: string | null;
  territory: string | null;
  status: DealerStatus;
};
type Page<T> = { items: T[]; total: number };
type EligibleCustomer = { id: number; code: string; name: string; customer_type: string; city: string | null };

const dealerFormSchema = z.object({
  dealer_code: z.string().trim().min(1, "Dealer code is required").max(64),
  customer_id: z.string().min(1, "Select a customer"),
  territory: z.string().max(255),
  status: z.enum(dealerStatuses),
});
type DealerFormValues = z.infer<typeof dealerFormSchema>;

const emptyDealer: DealerFormValues = {
  dealer_code: "",
  customer_id: "",
  territory: "",
  status: "pending",
};

export function DealersPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const canManage = can("dealers:manage");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Dealer | null>(null);

  const dealers = useQuery({
    queryKey: ["dealers", "list", page, search, statusFilter],
    queryFn: ({ signal }) => api<Page<Dealer>>("/dealers", {
      query: { page, page_size: PAGE_SIZE, q: search.trim(), status: statusFilter || undefined },
      signal,
    }),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const eligibleCustomers = useQuery({
    queryKey: ["dealers", "eligible-customers"],
    queryFn: ({ signal }) => api<EligibleCustomer[]>("/dealers/eligible-customers", { signal }),
    enabled: canManage && formOpen && !editing,
    retry: false,
  });
  const form = useForm<DealerFormValues>({
    resolver: zodResolver(dealerFormSchema),
    defaultValues: emptyDealer,
  });
  const save = useMutation({
    mutationFn: (values: DealerFormValues) => api<Dealer>(
      editing ? `/dealers/${editing.id}` : "/dealers",
      {
        method: editing ? "PUT" : "POST",
        body: editing
          ? { dealer_code: values.dealer_code, territory: values.territory || null, status: values.status }
          : { ...values, customer_id: Number(values.customer_id), territory: values.territory || null },
      },
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dealers", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["dealers", "eligible-customers"] }),
      ]);
      setFormOpen(false);
      setEditing(null);
      toast("success", editing ? "Dealer updated." : "Dealer profile created.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not save dealer."),
  });

  function openCreate() {
    setEditing(null);
    form.reset(emptyDealer);
    setFormOpen(true);
  }

  function openEdit(dealer: Dealer) {
    setEditing(dealer);
    form.reset({
      dealer_code: dealer.dealer_code,
      customer_id: String(dealer.customer_id),
      territory: dealer.territory ?? "",
      status: dealer.status,
    });
    setFormOpen(true);
  }

  const total = dealers.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const errors = form.formState.errors;

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">Dealers</h1>
          <p className="text-sm text-slate-500">Manage dealer profiles, territories, and account status.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="relative block w-full sm:w-64">
            <span className="sr-only">Search dealers</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
            <input
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
              placeholder="Search dealers…"
              className="w-full rounded-lg border border-ivory-300 bg-white py-2 pl-9 pr-3 text-sm"
            />
          </label>
          <select
            aria-label="Filter by dealer status"
            value={statusFilter}
            onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}
            className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All statuses</option>
            {dealerStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
          {canManage && <Button onClick={openCreate}><Plus className="h-4 w-4" aria-hidden /> Add dealer</Button>}
        </div>
      </div>

      <Card className="overflow-hidden">
        {dealers.isLoading && <LoadingState />}
        {dealers.isError && <ErrorState message={(dealers.error as Error).message} onRetry={() => void dealers.refetch()} />}
        {dealers.isSuccess && dealers.data.items.length === 0 && (
          <EmptyState
            title={search || statusFilter ? "No dealers found" : "No dealer profiles yet"}
            hint={canManage ? "Create a dealer profile from an existing customer marked dealer, distributor, or franchise." : undefined}
          />
        )}
        {dealers.isSuccess && dealers.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {["Dealer code", "Customer", "Type", "Territory", "Phone", "City", "Status"].map((label) => (
                    <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>
                  ))}
                  {canManage && <th scope="col" className="px-4 py-3 text-right font-medium">Actions</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {dealers.data.items.map((dealer) => (
                  <tr key={dealer.id} className="hover:bg-ivory-50">
                    <td className="px-4 py-3">{dealer.dealer_code}</td>
                    <td className="px-4 py-3">
                      <span className="block font-medium text-slate-900">{dealer.name}</span>
                      <span className="text-xs text-slate-500">{dealer.customer_code}</span>
                    </td>
                    <td className="px-4 py-3 capitalize">{dealer.customer_type}</td>
                    <td className="px-4 py-3">{dealer.territory || "—"}</td>
                    <td className="px-4 py-3">{dealer.phone || "—"}</td>
                    <td className="px-4 py-3">{dealer.city || "—"}</td>
                    <td className="px-4 py-3"><Badge>{dealer.status}</Badge></td>
                    {canManage && (
                      <td className="px-4 py-3 text-right">
                        <Button variant="ghost" className="px-2" onClick={() => openEdit(dealer)} aria-label={`Edit ${dealer.name}`}>
                          <Pencil className="h-4 w-4" aria-hidden />
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {dealers.isSuccess && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>Page {page} of {pages} · {total} dealers</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)} aria-label="Previous page">Previous</Button>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((value) => value + 1)} aria-label="Next page">Next</Button>
            </div>
          </div>
        )}
      </Card>

      <Modal open={formOpen} onClose={() => setFormOpen(false)} title={editing ? "Edit dealer" : "Add dealer"}>
        <form onSubmit={form.handleSubmit((values) => save.mutate(values))} className="space-y-4 p-6">
          <h2 className="text-lg font-semibold text-forest-900">{editing ? "Edit dealer profile" : "Create dealer profile"}</h2>
          {!editing && (
            <>
              {eligibleCustomers.isLoading && <LoadingState label="Loading eligible customers…" />}
              {eligibleCustomers.isError && <ErrorState message={(eligibleCustomers.error as Error).message} onRetry={() => void eligibleCustomers.refetch()} />}
              {eligibleCustomers.isSuccess && eligibleCustomers.data.length === 0 && (
                <p className="rounded-lg bg-ivory-100 p-3 text-sm text-slate-700">
                  First add a customer with type dealer, distributor, or franchise on the <Link className="font-medium text-forest-700 underline" to="/customers">Customers page</Link>.
                </p>
              )}
              {eligibleCustomers.isSuccess && eligibleCustomers.data.length > 0 && (
                <label className="block text-sm font-medium text-slate-700">
                  Customer
                  <select {...form.register("customer_id")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                    <option value="">Select customer</option>
                    {eligibleCustomers.data.map((customer) => (
                      <option key={customer.id} value={customer.id}>
                        {customer.name} · {customer.code} ({customer.customer_type})
                      </option>
                    ))}
                  </select>
                  {errors.customer_id?.message && <span role="alert" className="mt-1 block text-xs text-red-700">{errors.customer_id.message}</span>}
                </label>
              )}
            </>
          )}
          {editing && (
            <p className="rounded-lg bg-ivory-100 p-3 text-sm text-slate-700">
              Linked customer: <span className="font-medium">{editing.name} · {editing.customer_code}</span>
            </p>
          )}
          <Field label="Dealer code" {...form.register("dealer_code")} error={errors.dealer_code?.message} required />
          <Field label="Territory" {...form.register("territory")} error={errors.territory?.message} />
          <label className="block text-sm font-medium text-slate-700">
            Status
            <select {...form.register("status")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
              {dealerStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <div className="flex justify-end gap-2 border-t border-ivory-200 pt-4">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={save.isPending} disabled={!editing && (eligibleCustomers.isLoading || !eligibleCustomers.data?.length)}>
              {editing ? "Save changes" : "Create dealer"}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
