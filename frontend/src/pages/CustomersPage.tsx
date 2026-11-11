import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { ChevronLeft, ChevronRight, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { z } from "zod";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatCount, formatINR } from "../lib/format";

const PAGE_SIZE = 20;
const customerTypes = ["individual", "business", "dealer", "distributor", "franchise", "corporate"] as const;
type CustomerType = (typeof customerTypes)[number];
type State = { code: string; name: string };
type Customer = {
  id: number;
  code: string;
  name: string;
  customer_type: CustomerType;
  phone: string | null;
  email: string | null;
  gstin: string | null;
  pan: string | null;
  billing_address: string | null;
  shipping_address: string | null;
  state_code: string | null;
  city: string | null;
  opening_balance: string;
  credit_limit: string;
  payment_terms_days: number;
  branch_id: number | null;
  is_active: boolean;
};
type CustomerPageData = { items: Customer[]; total: number };

export const customerFormSchema = z.object({
  code: z.string().trim().min(1, "Customer code is required").max(64),
  name: z.string().trim().min(1, "Name is required").max(255),
  customer_type: z.enum(customerTypes),
  phone: z.string().trim().refine((value) => !value || (/^\+?[0-9().\-\s]{7,24}$/.test(value) && (value.match(/[0-9]/g)?.length ?? 0) >= 7), "Enter a valid phone number"),
  email: z.string().max(320).refine((value) => !value || z.email().safeParse(value).success, "Enter a valid email address"),
  gstin: z.string().trim().toUpperCase().refine((value) => !value || /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$/.test(value), "Enter a valid GSTIN format"),
  pan: z.string().trim().toUpperCase().refine((value) => !value || /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(value), "Enter a valid PAN format"),
  state_code: z.string(),
  city: z.string().max(128),
  billing_address: z.string(),
  shipping_address: z.string(),
  opening_balance: z.string().regex(/^-?\d{1,12}(\.\d{1,2})?$/, "Enter a valid amount"),
  credit_limit: z.string().regex(/^\d{1,12}(\.\d{1,2})?$/, "Enter a non-negative amount"),
  payment_terms_days: z.string().regex(/^\d+$/, "Enter a whole number of days"),
  is_active: z.boolean(),
}).superRefine((values, context) => {
  if (values.gstin && !values.state_code) {
    context.addIssue({ code: "custom", path: ["state_code"], message: "Select a state when entering a GSTIN" });
  } else if (values.gstin && values.gstin.slice(0, 2) !== values.state_code) {
    context.addIssue({ code: "custom", path: ["gstin"], message: "GSTIN state prefix must match the selected state" });
  }
});

export type CustomerFormValues = z.infer<typeof customerFormSchema>;

const emptyCustomer: CustomerFormValues = {
  code: "",
  name: "",
  customer_type: "business",
  phone: "",
  email: "",
  gstin: "",
  pan: "",
  state_code: "",
  city: "",
  billing_address: "",
  shipping_address: "",
  opening_balance: "0",
  credit_limit: "0",
  payment_terms_days: "0",
  is_active: true,
};

function customerForm(customer: Customer): CustomerFormValues {
  return {
    code: customer.code,
    name: customer.name,
    customer_type: customer.customer_type,
    phone: customer.phone ?? "",
    email: customer.email ?? "",
    gstin: customer.gstin ?? "",
    pan: customer.pan ?? "",
    state_code: customer.state_code ?? "",
    city: customer.city ?? "",
    billing_address: customer.billing_address ?? "",
    shipping_address: customer.shipping_address ?? "",
    opening_balance: customer.opening_balance,
    credit_limit: customer.credit_limit,
    payment_terms_days: String(customer.payment_terms_days),
    is_active: customer.is_active,
  };
}

function requestBody(values: CustomerFormValues) {
  return {
    ...values,
    payment_terms_days: Number(values.payment_terms_days),
    phone: values.phone || null,
    email: values.email || null,
    gstin: values.gstin || null,
    pan: values.pan || null,
    state_code: values.state_code || null,
    city: values.city || null,
    billing_address: values.billing_address || null,
    shipping_address: values.shipping_address || null,
  };
}

export function CustomersPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [queryText, setQueryText] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [deactivating, setDeactivating] = useState<Customer | null>(null);
  const canCreate = can("customers:create");
  const canUpdate = can("customers:update");
  const canDelete = can("customers:delete");

  const states = useQuery({
    queryKey: ["states"],
    queryFn: ({ signal }) => api<State[]>("/states", { signal }),
    retry: false,
  });

  useEffect(() => {
    const timer = setTimeout(() => {
      setQueryText(search.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const query = useQuery({
    queryKey: ["customers", "list", page, queryText, typeFilter, stateFilter, activeFilter],
    queryFn: ({ signal }) => api<CustomerPageData>("/customers", {
      query: {
        page,
        page_size: PAGE_SIZE,
        q: queryText,
        customer_type: typeFilter,
        state_code: stateFilter,
        is_active: activeFilter === "" ? undefined : activeFilter,
      },
      signal,
    }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const form = useForm<CustomerFormValues>({
    resolver: zodResolver(customerFormSchema),
    defaultValues: emptyCustomer,
  });
  const save = useMutation({
    mutationFn: ({ id, values }: { id: number | null; values: CustomerFormValues }) =>
      api<Customer>(id === null ? "/customers" : `/customers/${id}`, {
        method: id === null ? "POST" : "PUT",
        body: requestBody(values),
      }),
    onSuccess: async (_customer, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["customers", "list"] });
      setFormOpen(false);
      setEditing(null);
      toast("success", variables.id === null ? "Customer created." : "Customer updated.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not save customer."),
  });
  const deactivate = useMutation({
    mutationFn: (id: number) => api<void>(`/customers/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["customers", "list"] });
      setDeactivating(null);
      toast("success", "Customer deactivated; transaction history is retained.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not deactivate customer."),
  });

  function openCreate() {
    setEditing(null);
    form.reset(emptyCustomer);
    setFormOpen(true);
  }

  function openEdit(customer: Customer) {
    setEditing(customer);
    form.reset(customerForm(customer));
    setFormOpen(true);
  }

  const submit = form.handleSubmit((values) => save.mutate({ id: editing?.id ?? null, values }));
  const errors = form.formState.errors;
  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">Customers</h1>
          <p className="text-sm text-slate-500">Manage customer details, tax identity and payment terms.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="relative block w-full sm:w-64">
            <span className="sr-only">Search customers</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search customers…" className="w-full rounded-lg border border-ivory-300 bg-white py-2 pl-9 pr-3 text-sm" />
          </label>
          <select aria-label="Filter by customer type" value={typeFilter} onChange={(event) => { setTypeFilter(event.target.value); setPage(1); }} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
            <option value="">All types</option>
            {customerTypes.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <select aria-label="Filter by state" value={stateFilter} onChange={(event) => { setStateFilter(event.target.value); setPage(1); }} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
            <option value="">All states</option>
            {(states.data ?? []).map((state) => <option key={state.code} value={state.code}>{state.name}</option>)}
          </select>
          <select aria-label="Filter by active status" value={activeFilter} onChange={(event) => { setActiveFilter(event.target.value); setPage(1); }} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
            <option value="">Active and inactive</option>
            <option value="true">Active only</option>
            <option value="false">Inactive only</option>
          </select>
          {canCreate && <Button onClick={openCreate}><Plus className="h-4 w-4" aria-hidden /> Add customer</Button>}
        </div>
      </div>

      {states.isError && <ErrorState message={(states.error as Error).message} onRetry={() => states.refetch()} />}
      <Card className="overflow-hidden">
        {query.isLoading && <LoadingState />}
        {query.isError && <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />}
        {query.isSuccess && query.data.items.length === 0 && <EmptyState title={queryText ? "No results" : "No customers yet"} hint={queryText ? "Try a different search or filter." : canCreate ? "Add a customer to get started." : undefined} />}
        {query.isSuccess && query.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {["Code", "Name", "Type", "Phone", "State", "City", "Credit limit", "Status"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>)}
                  {(canUpdate || canDelete) && <th scope="col" className="px-4 py-3 text-right font-medium">Actions</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {query.data.items.map((customer) => (
                  <tr key={customer.id} className="hover:bg-ivory-50">
                    <td className="px-4 py-3">{customer.code}</td>
                    <td className="px-4 py-3 font-medium text-slate-900">{customer.name}</td>
                    <td className="px-4 py-3 capitalize">{customer.customer_type}</td>
                    <td className="px-4 py-3">{customer.phone || "—"}</td>
                    <td className="px-4 py-3">{states.data?.find((state) => state.code === customer.state_code)?.name ?? "—"}</td>
                    <td className="px-4 py-3">{customer.city || "—"}</td>
                    <td className="px-4 py-3 tabular-nums">{formatINR(customer.credit_limit)}</td>
                    <td className="px-4 py-3"><Badge>{customer.is_active ? "active" : "inactive"}</Badge></td>
                    {(canUpdate || canDelete) && (
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          {canUpdate && <Button variant="ghost" className="px-2" onClick={() => openEdit(customer)} aria-label={`Edit ${customer.name}`}><Pencil className="h-4 w-4" aria-hidden /></Button>}
                          {canDelete && customer.is_active && <Button variant="ghost" className="px-2 text-red-700 hover:bg-red-50" onClick={() => setDeactivating(customer)} aria-label={`Deactivate ${customer.name}`}><Trash2 className="h-4 w-4" aria-hidden /></Button>}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {query.isSuccess && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>Page {page} of {pages} · {formatCount(total)} customers</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((current) => current - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((current) => current + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button>
            </div>
          </div>
        )}
      </Card>

      <Modal open={formOpen && (editing ? canUpdate : canCreate)} onClose={() => { if (!save.isPending) setFormOpen(false); }} title={editing ? "Edit customer" : "Add customer"}>
        <form onSubmit={submit} className="max-h-[78vh] overflow-y-auto p-6">
          <h2 className="text-lg font-semibold text-slate-900">{editing ? "Edit customer" : "Add customer"}</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Customer code" {...form.register("code")} error={errors.code?.message} required maxLength={64} />
            <Field label="Name" {...form.register("name")} error={errors.name?.message} required maxLength={255} />
            <label className="block text-sm font-medium text-slate-700">Customer type
              <select {...form.register("customer_type")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">{customerTypes.map((type) => <option key={type} value={type}>{type}</option>)}</select>
              {errors.customer_type?.message && <span role="alert" className="mt-1 block text-xs text-red-700">{errors.customer_type.message}</span>}
            </label>
            <label className="block text-sm font-medium text-slate-700">State
              <select {...form.register("state_code")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"><option value="">Select state</option>{(states.data ?? []).map((state) => <option key={state.code} value={state.code}>{state.code} · {state.name}</option>)}</select>
              {errors.state_code?.message && <span role="alert" className="mt-1 block text-xs text-red-700">{errors.state_code.message}</span>}
            </label>
            <Field label="Phone" {...form.register("phone")} error={errors.phone?.message} maxLength={64} />
            <Field label="Email" type="email" {...form.register("email")} error={errors.email?.message} maxLength={320} />
            <Field label="GSTIN" {...form.register("gstin")} error={errors.gstin?.message} maxLength={32} />
            <Field label="PAN" {...form.register("pan")} error={errors.pan?.message} maxLength={16} />
            <Field label="City" {...form.register("city")} error={errors.city?.message} maxLength={128} />
            <Field label="Opening balance" type="number" step="0.01" {...form.register("opening_balance")} error={errors.opening_balance?.message} required />
            <Field label="Credit limit" type="number" min="0" step="0.01" {...form.register("credit_limit")} error={errors.credit_limit?.message} required />
            <Field label="Payment terms (days)" type="number" min="0" step="1" {...form.register("payment_terms_days")} error={errors.payment_terms_days?.message} required />
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" {...form.register("is_active")} />Active customer</label>
            <label className="text-sm font-medium text-slate-700 sm:col-span-2">Billing address
              <textarea {...form.register("billing_address")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" rows={2} />
            </label>
            <label className="text-sm font-medium text-slate-700 sm:col-span-2">Shipping address
              <textarea {...form.register("shipping_address")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" rows={2} />
            </label>
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)} disabled={save.isPending}>Cancel</Button>
            <Button type="submit" loading={save.isPending}>{editing ? "Save changes" : "Create customer"}</Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deactivating !== null}
        title="Deactivate customer"
        message={`Deactivate ${deactivating?.name ?? "this customer"}? Existing invoice and transaction history will be retained.`}
        confirmLabel="Deactivate customer"
        danger
        onCancel={() => { if (!deactivate.isPending) setDeactivating(null); }}
        onConfirm={async () => { if (deactivating) await deactivate.mutateAsync(deactivating.id); }}
      />
    </div>
  );
}
