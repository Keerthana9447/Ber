import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatCount, formatINR } from "../lib/format";

const PAGE_SIZE = 20;

type HsnSacOption = {
  code: string;
  kind: "HSN" | "SAC";
  description: string;
  default_gst_rate: string | null;
  default_cess_rate: string;
};
type ProductOptions = {
  categories: { id: number; name: string; is_serialized: boolean }[];
  hsn_sac_codes: HsnSacOption[];
  units: { code: string; name: string }[];
  gst_rates: { id: number; rate: string; description: string | null }[];
  warehouses: { id: number; code: string; name: string; branch_id: number | null }[];
};
type Product = {
  id: number;
  sku: string;
  code: string | null;
  name: string;
  category_id: number;
  brand: string | null;
  model: string | null;
  description: string | null;
  hsn_sac: string;
  unit: string;
  purchase_price: string;
  selling_price: string;
  gst_rate: string;
  cess_rate: string;
  min_stock: string;
  is_serialized: boolean;
  is_batch_tracked: boolean;
  is_service: boolean;
  is_active: boolean;
};
type ProductPageData = { items: Product[]; total: number };
type ProductForm = {
  sku: string;
  code: string;
  name: string;
  category_id: string;
  brand: string;
  model: string;
  description: string;
  hsn_sac: string;
  unit: string;
  purchase_price: string;
  selling_price: string;
  gst_rate: string;
  cess_rate: string;
  min_stock: string;
  is_serialized: boolean;
  is_batch_tracked: boolean;
  is_service: boolean;
  is_active: boolean;
};
type ReferenceForm = "category" | "gst" | "hsn" | null;

const emptyProduct: ProductForm = {
  sku: "",
  code: "",
  name: "",
  category_id: "",
  brand: "",
  model: "",
  description: "",
  hsn_sac: "",
  unit: "",
  purchase_price: "0",
  selling_price: "0",
  gst_rate: "",
  cess_rate: "0",
  min_stock: "0",
  is_serialized: false,
  is_batch_tracked: false,
  is_service: false,
  is_active: true,
};

function formFromProduct(product: Product): ProductForm {
  return {
    sku: product.sku,
    code: product.code ?? "",
    name: product.name,
    category_id: String(product.category_id),
    brand: product.brand ?? "",
    model: product.model ?? "",
    description: product.description ?? "",
    hsn_sac: product.hsn_sac,
    unit: product.unit,
    purchase_price: product.purchase_price,
    selling_price: product.selling_price,
    gst_rate: product.gst_rate,
    cess_rate: product.cess_rate,
    min_stock: product.min_stock,
    is_serialized: product.is_serialized,
    is_batch_tracked: product.is_batch_tracked,
    is_service: product.is_service,
    is_active: product.is_active,
  };
}

function productBody(form: ProductForm) {
  return {
    ...form,
    code: form.code || null,
    category_id: Number(form.category_id),
    brand: form.brand || null,
    model: form.model || null,
    description: form.description || null,
  };
}

export function ProductsPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [queryText, setQueryText] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [form, setForm] = useState<ProductForm>(emptyProduct);
  const [deleting, setDeleting] = useState<Product | null>(null);
  const [referenceForm, setReferenceForm] = useState<ReferenceForm>(null);
  const canManageProducts = can("products:manage");
  const canManageSettings = can("settings:manage");

  const options = useQuery({
    queryKey: ["product-options"],
    queryFn: ({ signal }) => api<ProductOptions>("/product-options", { signal }),
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
    queryKey: ["products", "list", page, queryText],
    queryFn: ({ signal }) => api<ProductPageData>("/products", { query: { page, page_size: PAGE_SIZE, q: queryText }, signal }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const refreshData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["products", "list"] }),
      queryClient.invalidateQueries({ queryKey: ["product-options"] }),
      queryClient.invalidateQueries({ queryKey: ["inventory", "list"] }),
    ]);
  };

  const save = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: ReturnType<typeof productBody> }) =>
      api<Product>(id === null ? "/products" : `/products/${id}`, {
        method: id === null ? "POST" : "PUT",
        body,
      }),
    onSuccess: async (_product, variables) => {
      await refreshData();
      setFormOpen(false);
      setEditing(null);
      toast("success", variables.id === null ? "Product created." : "Product updated.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not save product."),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/products/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await refreshData();
      setDeleting(null);
      toast("success", "Product deactivated.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not deactivate product."),
  });

  const addReference = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) =>
      api(path, { method: "POST", body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["product-options"] });
      setReferenceForm(null);
      toast("success", "Reference added.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not add reference."),
  });

  function openCreate() {
    setEditing(null);
    setForm(emptyProduct);
    setFormOpen(true);
  }

  function openEdit(product: Product) {
    setEditing(product);
    setForm(formFromProduct(product));
    setFormOpen(true);
  }

  function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({ id: editing?.id ?? null, body: productBody(form) });
  }

  function submitReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (referenceForm === "category") {
      addReference.mutate({
        path: "/product-categories",
        body: { name: data.get("name"), is_serialized: data.get("is_serialized") === "on" },
      });
    } else if (referenceForm === "gst") {
      addReference.mutate({
        path: "/gst-rates",
        body: { rate: data.get("rate"), description: data.get("description") || null },
      });
    } else if (referenceForm === "hsn") {
      addReference.mutate({
        path: "/hsn-sac-codes",
        body: {
          code: data.get("code"),
          kind: data.get("kind"),
          description: data.get("description"),
          default_gst_rate: data.get("default_gst_rate") || null,
          default_cess_rate: data.get("default_cess_rate") || "0",
        },
      });
    }
  }

  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const availableHsn = options.data?.hsn_sac_codes.filter((item) => item.kind === (form.is_service ? "SAC" : "HSN")) ?? [];
  const selectedHsn = availableHsn.find((item) => item.code === form.hsn_sac);
  const allowedRates = options.data?.gst_rates.filter(
    (item) => !selectedHsn?.default_gst_rate || item.rate === selectedHsn.default_gst_rate,
  ) ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">Products</h1>
          <p className="text-sm text-slate-500">Manage saleable products, services and tax classifications.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canManageProducts && (
            <Button variant="secondary" onClick={() => setReferenceForm("category")}>
              Add category
            </Button>
          )}
          {canManageSettings && (
            <>
              <Button variant="secondary" onClick={() => setReferenceForm("gst")}>Add GST rate</Button>
              <Button variant="secondary" onClick={() => setReferenceForm("hsn")}>Add HSN/SAC</Button>
            </>
          )}
          <label className="relative block w-full sm:w-64">
            <span className="sr-only">Search products</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search products…"
              className="w-full rounded-lg border border-ivory-300 bg-white py-2 pl-9 pr-3 text-sm"
            />
          </label>
          {canManageProducts && (
            <Button onClick={openCreate}><Plus className="h-4 w-4" aria-hidden /> Add product</Button>
          )}
        </div>
      </div>

      {options.isError && <ErrorState message={(options.error as Error).message} onRetry={() => options.refetch()} />}
      <Card className="overflow-hidden">
        {query.isLoading && <LoadingState />}
        {query.isError && <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />}
        {query.isSuccess && query.data.items.length === 0 && (
          <EmptyState
            title={queryText ? "No results" : "No products yet"}
            hint={queryText ? "Try a different search." : "Configure product categories and tax references, then add a product."}
          />
        )}
        {query.isSuccess && query.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {["SKU", "Name", "Brand", "HSN/SAC", "GST %", "Price", "Active"].map((label) => (
                    <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>
                  ))}
                  {canManageProducts && <th scope="col" className="px-4 py-3 text-right font-medium">Actions</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {query.data.items.map((product) => (
                  <tr key={product.id} className="hover:bg-ivory-50">
                    <td className="px-4 py-3">{product.sku}</td>
                    <td className="px-4 py-3 font-medium text-slate-900">{product.name}</td>
                    <td className="px-4 py-3">{product.brand || "—"}</td>
                    <td className="px-4 py-3">{product.hsn_sac}</td>
                    <td className="px-4 py-3 tabular-nums">{product.gst_rate}%</td>
                    <td className="px-4 py-3 tabular-nums">{formatINR(product.selling_price)}</td>
                    <td className="px-4 py-3"><Badge>{product.is_active ? "active" : "inactive"}</Badge></td>
                    {canManageProducts && (
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" className="px-2" onClick={() => openEdit(product)} aria-label={`Edit ${product.name}`}>
                            <Pencil className="h-4 w-4" aria-hidden />
                          </Button>
                          <Button variant="ghost" className="px-2 text-red-700 hover:bg-red-50" onClick={() => setDeleting(product)} aria-label={`Deactivate ${product.name}`}>
                            <Trash2 className="h-4 w-4" aria-hidden />
                          </Button>
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
            <span>Page {page} of {pages} · {formatCount(total)} products</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((current) => current - 1)} aria-label="Previous page">
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage((current) => current + 1)} aria-label="Next page">
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Modal open={formOpen && canManageProducts} onClose={() => { if (!save.isPending) setFormOpen(false); }} title={editing ? "Edit product" : "Add product"}>
        <form onSubmit={submitProduct} className="max-h-[78vh] overflow-y-auto p-6">
          <h2 className="text-lg font-semibold text-slate-900">{editing ? "Edit product" : "Add product"}</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="SKU" value={form.sku} onChange={(event) => setForm({ ...form, sku: event.target.value })} required maxLength={128} />
            <Field label="Name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required maxLength={255} />
            <label className="block text-sm font-medium text-slate-700">
              Category
              <select value={form.category_id} onChange={(event) => setForm({ ...form, category_id: event.target.value })} required className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                <option value="">Select category</option>
                {(options.data?.categories ?? []).map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              HSN/SAC
              <select
                value={form.hsn_sac}
                onChange={(event) => {
                  const selected = availableHsn.find((item) => item.code === event.target.value);
                  setForm({
                    ...form,
                    hsn_sac: event.target.value,
                    gst_rate: selected?.default_gst_rate ?? form.gst_rate,
                    cess_rate: selected?.default_cess_rate ?? "0",
                  });
                }}
                required
                className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"
              >
                <option value="">Select {form.is_service ? "SAC" : "HSN"} code</option>
                {availableHsn.map((item) => <option key={item.code} value={item.code}>{item.code} · {item.description}</option>)}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Unit
              <select value={form.unit} onChange={(event) => setForm({ ...form, unit: event.target.value })} required className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                <option value="">Select unit</option>
                {(options.data?.units ?? []).map((unit) => <option key={unit.code} value={unit.code}>{unit.code} · {unit.name}</option>)}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              GST rate
              <select value={form.gst_rate} onChange={(event) => setForm({ ...form, gst_rate: event.target.value })} required className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                <option value="">Select configured rate</option>
                {allowedRates.map((rate) => <option key={rate.id} value={rate.rate}>{rate.rate}%{rate.description ? ` · ${rate.description}` : ""}</option>)}
              </select>
            </label>
            <Field label="Cess rate (%)" type="number" min="0" max="100" step="0.01" value={form.cess_rate} onChange={(event) => setForm({ ...form, cess_rate: event.target.value })} required />
            <Field label="Purchase price" type="number" min="0" step="0.01" value={form.purchase_price} onChange={(event) => setForm({ ...form, purchase_price: event.target.value })} required />
            <Field label="Selling price" type="number" min="0" step="0.01" value={form.selling_price} onChange={(event) => setForm({ ...form, selling_price: event.target.value })} required />
            <Field label="Minimum stock" type="number" min="0" step="0.001" value={form.min_stock} onChange={(event) => setForm({ ...form, min_stock: event.target.value })} required />
            <Field label="Code" value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} maxLength={128} />
            <Field label="Brand" value={form.brand} onChange={(event) => setForm({ ...form, brand: event.target.value })} maxLength={128} />
            <Field label="Model" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} maxLength={128} />
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={form.is_service} onChange={(event) => setForm({ ...form, is_service: event.target.checked, hsn_sac: "" })} />Service</label>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={form.is_serialized} onChange={(event) => setForm({ ...form, is_serialized: event.target.checked })} disabled={form.is_service} />Serialized item</label>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={form.is_batch_tracked} onChange={(event) => setForm({ ...form, is_batch_tracked: event.target.checked })} disabled={form.is_service} />Batch tracked</label>
            <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={form.is_active} onChange={(event) => setForm({ ...form, is_active: event.target.checked })} />Active</label>
            <label className="text-sm font-medium text-slate-700 sm:col-span-2">
              Description
              <textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" rows={2} />
            </label>
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)} disabled={save.isPending}>Cancel</Button>
            <Button type="submit" loading={save.isPending}>{editing ? "Save changes" : "Create product"}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={referenceForm !== null} onClose={() => { if (!addReference.isPending) setReferenceForm(null); }} title="Add product reference">
        <form key={referenceForm ?? "none"} onSubmit={submitReference} className="space-y-4 p-6">
          <h2 className="text-lg font-semibold text-slate-900">
            {referenceForm === "category" ? "Add product category" : referenceForm === "gst" ? "Add configured GST rate" : "Add HSN/SAC code"}
          </h2>
          {referenceForm === "category" && (
            <>
              <Field label="Category name" name="name" required maxLength={255} />
              <label className="flex items-center gap-2 text-sm text-slate-700"><input name="is_serialized" type="checkbox" />Serialized category</label>
            </>
          )}
          {referenceForm === "gst" && (
            <>
              <Field label="GST rate (%)" name="rate" type="number" min="0" max="100" step="0.01" required />
              <Field label="Description" name="description" maxLength={255} />
              <p className="text-xs text-slate-500">Enter only a rate verified for your business by a qualified tax professional.</p>
            </>
          )}
          {referenceForm === "hsn" && (
            <>
              <Field label="HSN/SAC code" name="code" inputMode="numeric" minLength={4} maxLength={8} pattern="[0-9]{4,8}" required />
              <label className="block text-sm font-medium text-slate-700">
                Code kind
                <select name="kind" className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                  <option value="HSN">HSN (goods)</option>
                  <option value="SAC">SAC (services)</option>
                </select>
              </label>
              <Field label="Description" name="description" required maxLength={1000} />
              <label className="block text-sm font-medium text-slate-700">
                Default GST rate
                <select name="default_gst_rate" className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                  <option value="">No default</option>
                  {(options.data?.gst_rates ?? []).map((rate) => <option key={rate.id} value={rate.rate}>{rate.rate}%</option>)}
                </select>
              </label>
              <Field label="Default cess rate (%)" name="default_cess_rate" type="number" min="0" max="100" step="0.01" defaultValue="0" required />
              <p className="text-xs text-slate-500">Have the code and tax treatment verified before entering them.</p>
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setReferenceForm(null)} disabled={addReference.isPending}>Cancel</Button>
            <Button type="submit" loading={addReference.isPending}>Add reference</Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title="Deactivate product"
        message={`Deactivate ${deleting?.name ?? "this product"}? It will be retained for transaction history.`}
        confirmLabel="Deactivate product"
        danger
        onCancel={() => { if (!remove.isPending) setDeleting(null); }}
        onConfirm={async () => { if (deleting) await remove.mutateAsync(deleting.id); }}
      />
    </div>
  );
}
