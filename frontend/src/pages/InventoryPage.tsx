import { useEffect, useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingState, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatCount } from "../lib/format";

const PAGE_SIZE = 20;

type Warehouse = { id: number; code: string; name: string; branch_id: number | null };
type Product = {
  id: number;
  sku: string;
  name: string;
  is_active: boolean;
  is_service: boolean;
  is_serialized: boolean;
  is_batch_tracked: boolean;
};
type InventoryRow = {
  product_id: number;
  sku: string;
  product_name: string;
  warehouse_id: number;
  warehouse_name: string;
  quantity: string;
  min_stock: string;
  low_stock: boolean;
};
type InventoryPageData = { items: InventoryRow[]; total: number };
type ProductPageData = { items: Product[]; total: number };
type ProductOptions = { warehouses: Warehouse[] };
type UnvaluedInventory = {
  product_id: number;
  product_name: string;
  sku: string;
  warehouse_id: number;
  warehouse_name: string;
  quantity: string;
};

export function InventoryPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can, user } = useAuth();
  const canManage = can("inventory:manage");
  const isAdmin = user?.role === "Admin" || user?.role === "Super Admin";
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [queryText, setQueryText] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [adjustmentOpen, setAdjustmentOpen] = useState(false);
  const [warehouseOpen, setWarehouseOpen] = useState(false);
  const [productSearch, setProductSearch] = useState("");
  const [openingValuationOpen, setOpeningValuationOpen] = useState(false);
  const [openingCosts, setOpeningCosts] = useState<Record<string, string>>({});

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
    queryKey: ["inventory", "list", page, queryText, warehouseId],
    queryFn: ({ signal }) =>
      api<InventoryPageData>("/inventory", {
        query: { page, page_size: PAGE_SIZE, q: queryText, warehouse_id: warehouseId },
        signal,
      }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const unvaluedInventory = useQuery({
    queryKey: ["inventory", "opening-valuations"],
    queryFn: ({ signal }) => api<{ items: UnvaluedInventory[] }>("/inventory/opening-valuations", { signal }),
    enabled: openingValuationOpen && isAdmin,
    retry: false,
  });

  const postOpeningValuation = useMutation({
    mutationFn: (lines: { product_id: number; warehouse_id: number; unit_cost: string }[]) =>
      api<{ total_value: string }>("/inventory/opening-valuations", { method: "POST", body: { lines } }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["inventory", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory", "opening-valuations"] }),
      ]);
      setOpeningCosts({});
      setOpeningValuationOpen(false);
      toast("success", `Opening valuation posted (${result.total_value}).`);
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not post opening valuation."),
  });

  const products = useQuery({
    queryKey: ["inventory-adjustment-products", productSearch],
    queryFn: ({ signal }) =>
      api<ProductPageData>("/products", {
        query: { page: 1, page_size: 100, q: productSearch },
        signal,
      }),
    enabled: adjustmentOpen && canManage,
    retry: false,
  });

  const adjustment = useMutation({
    mutationFn: (body: { product_id: number; warehouse_id: number; quantity_delta: string; unit_cost?: string; notes?: string }) =>
      api("/inventory/adjustments", { method: "POST", body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inventory", "list"] });
      setAdjustmentOpen(false);
      setProductSearch("");
      toast("success", "Stock adjustment posted.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not adjust stock."),
  });

  const createWarehouse = useMutation({
    mutationFn: (body: { code: string; name: string }) =>
      api("/warehouses", { method: "POST", body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["product-options"] });
      setWarehouseOpen(false);
      toast("success", "Warehouse created.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not create warehouse."),
  });

  function submitAdjustment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    adjustment.mutate({
      product_id: Number(data.get("product_id")),
      warehouse_id: Number(data.get("warehouse_id")),
      quantity_delta: String(data.get("quantity_delta")),
      unit_cost: String(data.get("unit_cost") || "") || undefined,
      notes: String(data.get("notes") || "") || undefined,
    });
  }

  function submitWarehouse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    createWarehouse.mutate({
      code: String(data.get("code")),
      name: String(data.get("name")),
    });
  }

  const total = query.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const warehouses = options.data?.warehouses ?? [];
  const eligibleProducts = (products.data?.items ?? []).filter(
    (product) => product.is_active && !product.is_service && !product.is_serialized && !product.is_batch_tracked,
  );

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">Stock</h1>
          <p className="text-sm text-slate-500">On-hand quantities by warehouse, with an immutable adjustment ledger.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="relative block w-full sm:w-64">
            <span className="sr-only">Search stock</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search SKU or product…"
              className="w-full rounded-lg border border-ivory-300 bg-white py-2 pl-9 pr-3 text-sm"
            />
          </label>
          <select
            aria-label="Filter by warehouse"
            value={warehouseId}
            onChange={(event) => { setWarehouseId(event.target.value); setPage(1); }}
            className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All warehouses</option>
            {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
          </select>
          {canManage && (
            <>
              <Button variant="secondary" onClick={() => setWarehouseOpen(true)}><Plus className="h-4 w-4" aria-hidden /> Add warehouse</Button>
              <Button onClick={() => setAdjustmentOpen(true)}><Plus className="h-4 w-4" aria-hidden /> Adjust stock</Button>
              {isAdmin && <Button variant="secondary" onClick={() => setOpeningValuationOpen(true)}>Opening valuation</Button>}
            </>
          )}
        </div>
      </div>

      {options.isError && <ErrorState message={(options.error as Error).message} onRetry={() => options.refetch()} />}
      <Card className="overflow-hidden">
        {query.isLoading && <LoadingState />}
        {query.isError && <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />}
        {query.isSuccess && query.data.items.length === 0 && (
          <EmptyState
            title={queryText || warehouseId ? "No stock found" : "No stock recorded yet"}
            hint={warehouses.length === 0 ? "Create a warehouse, add products, then record opening stock." : "Use Adjust stock to record an opening quantity or correction."}
          />
        )}
        {query.isSuccess && query.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  {["SKU", "Product", "Warehouse", "On hand", "Minimum", "Status"].map((label) => (
                    <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ivory-200">
                {query.data.items.map((row) => (
                  <tr key={`${row.product_id}:${row.warehouse_id}`} className="hover:bg-ivory-50">
                    <td className="px-4 py-3">{row.sku}</td>
                    <td className="px-4 py-3 font-medium text-slate-900">{row.product_name}</td>
                    <td className="px-4 py-3">{row.warehouse_name}</td>
                    <td className="px-4 py-3 tabular-nums">{formatCount(row.quantity)}</td>
                    <td className="px-4 py-3 tabular-nums">{formatCount(row.min_stock)}</td>
                    <td className="px-4 py-3"><Badge>{row.low_stock ? "low stock" : "in stock"}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {query.isSuccess && total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>Page {page} of {pages} · {formatCount(total)} stock records</span>
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

      <Modal open={adjustmentOpen && canManage} onClose={() => { if (!adjustment.isPending) setAdjustmentOpen(false); }} title="Adjust stock">
        <form onSubmit={submitAdjustment} className="space-y-4 p-6">
          <h2 className="text-lg font-semibold text-slate-900">Adjust stock</h2>
          <label className="block text-sm font-medium text-slate-700">
            Search products
            <input
              value={productSearch}
              onChange={(event) => setProductSearch(event.target.value)}
              placeholder="Search by SKU or product name…"
              className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"
            />
          </label>
          {products.isError && <p role="alert" className="text-sm text-red-700">{(products.error as Error).message}</p>}
          <label className="block text-sm font-medium text-slate-700">
            Product
            <select name="product_id" required className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
              <option value="">Select product</option>
              {eligibleProducts.map((product) => <option key={product.id} value={product.id}>{product.sku} · {product.name}</option>)}
            </select>
          </label>
          {products.isSuccess && eligibleProducts.length === 0 && (
            <p className="text-xs text-slate-500">No active, non-serialized stock product matched. Serialized and batch-tracked items need detailed receiving workflows.</p>
          )}
          <label className="block text-sm font-medium text-slate-700">
            Warehouse
            <select name="warehouse_id" required className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
              <option value="">Select warehouse</option>
              {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.code} · {warehouse.name}</option>)}
            </select>
          </label>
          <Field label="Quantity change (+ adds stock, − removes stock)" name="quantity_delta" type="number" step="0.001" required />
          <Field label="Unit cost (optional)" name="unit_cost" type="number" min="0" step="0.0001" />
          <Field label="Reason / notes" name="notes" maxLength={1000} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setAdjustmentOpen(false)} disabled={adjustment.isPending}>Cancel</Button>
            <Button type="submit" loading={adjustment.isPending} disabled={!eligibleProducts.length || !warehouses.length}>Post adjustment</Button>
          </div>
        </form>
      </Modal>

      <Modal open={warehouseOpen && canManage} onClose={() => { if (!createWarehouse.isPending) setWarehouseOpen(false); }} title="Add warehouse">
        <form onSubmit={submitWarehouse} className="space-y-4 p-6">
          <h2 className="text-lg font-semibold text-slate-900">Add warehouse</h2>
          <Field label="Warehouse code" name="code" required maxLength={64} />
          <Field label="Warehouse name" name="name" required maxLength={255} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setWarehouseOpen(false)} disabled={createWarehouse.isPending}>Cancel</Button>
            <Button type="submit" loading={createWarehouse.isPending}>Create warehouse</Button>
          </div>
        </form>
      </Modal>

      <Modal open={openingValuationOpen && isAdmin} onClose={() => { if (!postOpeningValuation.isPending) setOpeningValuationOpen(false); }} title="Opening valuation">
        <form
          className="max-h-[76vh] space-y-4 overflow-y-auto p-6"
          onSubmit={(event) => {
            event.preventDefault();
            const lines = (unvaluedInventory.data?.items ?? [])
              .filter((row) => openingCosts[`${row.product_id}:${row.warehouse_id}`])
              .map((row) => ({
                product_id: row.product_id,
                warehouse_id: row.warehouse_id,
                unit_cost: openingCosts[`${row.product_id}:${row.warehouse_id}`],
              }));
            if (lines.length) postOpeningValuation.mutate(lines);
          }}
        >
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Opening stock valuation</h2>
            <p className="text-sm text-slate-600">
              Enter the verified unit cost for each existing stock balance. One Inventory / Owner’s Equity journal entry is posted for this batch. Corrections require a reversal.
            </p>
          </div>
          {unvaluedInventory.isLoading && <LoadingState />}
          {unvaluedInventory.isError && <ErrorState message={(unvaluedInventory.error as Error).message} onRetry={() => unvaluedInventory.refetch()} />}
          {unvaluedInventory.isSuccess && unvaluedInventory.data.items.length === 0 && <EmptyState title="No unvalued on-hand stock" />}
          {unvaluedInventory.data?.items.map((row) => {
            const key = `${row.product_id}:${row.warehouse_id}`;
            return (
              <label key={key} className="grid gap-2 text-sm sm:grid-cols-[1fr_9rem]">
                <span>
                  {row.sku} · {row.product_name}
                  <span className="block text-xs text-slate-500">{row.warehouse_name} · {formatCount(row.quantity)} on hand</span>
                </span>
                <input
                  type="number"
                  min="0.0001"
                  step="0.0001"
                  required
                  aria-label={`Unit cost for ${row.product_name} at ${row.warehouse_name}`}
                  value={openingCosts[key] ?? ""}
                  onChange={(event) => setOpeningCosts((current) => ({ ...current, [key]: event.target.value }))}
                  className="rounded-lg border border-ivory-300 px-3 py-2"
                />
              </label>
            );
          })}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpeningValuationOpen(false)} disabled={postOpeningValuation.isPending}>Cancel</Button>
            <Button
              type="submit"
              loading={postOpeningValuation.isPending}
              disabled={!unvaluedInventory.data?.items.some((row) => openingCosts[`${row.product_id}:${row.warehouse_id}`])}
            >
              Post batch
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
