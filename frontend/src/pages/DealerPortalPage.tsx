import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { formatCount, formatINR } from "../lib/format";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingState } from "../components/ui";

type DealerProfile = { dealer_code: string; name: string; territory: string | null; status: string };
type DealerProduct = {
  product_id: number;
  sku: string;
  name: string;
  hsn_sac: string;
  gst_rate: string;
  unit: string;
  price: string;
  has_dealer_price: boolean;
};
type Page<T> = { items: T[]; total: number };
type OrderLine = { product_id: number; quantity: string };
type DealerOrder = {
  id: number;
  number: string;
  order_date: string;
  status: string;
  grand_total: string;
  lines: Array<{ id: number; product_id: number; description: string | null; quantity: string; unit_price: string }>;
};
type Statement = {
  items: Array<{ id: number; date: string; type: string; reference: string; debit: string; credit: string; balance: string }>;
  total: number;
  closing_balance: string;
};
const PAGE_SIZE = 20;

export function dealerOrderRequestBody(lines: OrderLine[]) {
  return { lines: lines.map(({ product_id, quantity }) => ({ product_id, quantity })) };
}

export function DealerOrderHistory({ orders }: { orders: DealerOrder[] }) {
  if (!orders.length) return <EmptyState title="No orders yet" hint="Orders you place will appear here with their latest status." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[600px] text-left text-sm">
        <thead className="bg-ivory-100 text-xs uppercase text-slate-500">
          <tr>{["Order", "Date", "Items", "Total", "Status"].map((item) => <th className="px-4 py-3" key={item}>{item}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-ivory-200">
          {orders.map((order) => (
            <tr key={order.id}>
              <td className="px-4 py-3 font-medium">{order.number}</td>
              <td className="px-4 py-3">{order.order_date}</td>
              <td className="px-4 py-3">{formatCount(order.lines.length)}</td>
              <td className="px-4 py-3 tabular-nums">{formatINR(order.grand_total)}</td>
              <td className="px-4 py-3"><Badge>{order.status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DealerPortalPage() {
  const [page, setPage] = useState(1);
  const [orderPage, setOrderPage] = useState(1);
  const [statementPage, setStatementPage] = useState(1);
  const [productId, setProductId] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [cart, setCart] = useState<OrderLine[]>([]);
  const queryClient = useQueryClient();
  const profile = useQuery({
    queryKey: ["dealer-portal", "profile"],
    queryFn: ({ signal }) => api<DealerProfile>("/dealer/portal/profile", { signal }),
    retry: false,
  });
  const orders = useQuery({
    queryKey: ["dealer-portal", "orders", orderPage],
    queryFn: ({ signal }) => api<Page<DealerOrder>>("/dealer/portal/orders", { query: { page: orderPage, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const statement = useQuery({
    queryKey: ["dealer-portal", "statement", statementPage],
    queryFn: ({ signal }) => api<Statement>("/dealer/portal/statement", { query: { page: statementPage, page_size: 50 }, signal }),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const createOrder = useMutation({
    mutationFn: () => api<DealerOrder>("/dealer/portal/orders", {
      method: "POST",
      body: dealerOrderRequestBody(cart),
    }),
    onSuccess: async () => {
      setCart([]);
      setProductId("");
      await queryClient.invalidateQueries({ queryKey: ["dealer-portal", "orders"] });
    },
  });
  const products = useQuery({
    queryKey: ["dealer-portal", "products", page],
    queryFn: ({ signal }) => api<Page<DealerProduct>>("/dealer/portal/products", { query: { page, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Dealer portal</h1>
        <p className="text-sm text-slate-500">Your account, product catalog, and dealer-specific pricing.</p>
      </header>
      {profile.isLoading && <LoadingState label="Loading dealer profile…" />}
      {profile.isError && <ErrorState message={profile.error.message} onRetry={() => void profile.refetch()} />}
      {profile.data && (
        <Card className="flex flex-wrap items-center justify-between gap-4 p-5">
          <div>
            <h2 className="text-lg font-semibold">{profile.data.name}</h2>
            <p className="text-sm text-slate-500">Dealer code {profile.data.dealer_code}{profile.data.territory ? ` · ${profile.data.territory}` : ""}</p>
          </div>
          <Badge>{profile.data.status}</Badge>
        </Card>
      )}
      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-4"><h2 className="font-semibold">Products and your prices</h2></div>
        {products.isLoading && <LoadingState />}
        {products.isError && <ErrorState message={products.error.message} onRetry={() => void products.refetch()} />}
        {products.data?.items.length === 0 && <EmptyState title="No products available" />}
        {products.data && products.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-sm">
                <thead className="bg-ivory-100 text-xs uppercase text-slate-500">
                  <tr>{["SKU", "Product", "HSN/SAC", "GST", "Unit", "Your price"].map((item) => <th className="px-4 py-3" key={item}>{item}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-ivory-200">
                  {products.data.items.map((product) => (
                    <tr key={product.product_id}>
                      <td className="px-4 py-3">{product.sku}</td>
                      <td className="px-4 py-3 font-medium">{product.name}</td>
                      <td className="px-4 py-3">{product.hsn_sac}</td>
                      <td className="px-4 py-3">{formatCount(product.gst_rate)}%</td>
                      <td className="px-4 py-3">{product.unit}</td>
                      <td className="px-4 py-3 tabular-nums">{formatINR(product.price)}{product.has_dealer_price && <Badge>Dealer price</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {products.data.total > PAGE_SIZE && (
              <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
                <span>Page {page} · {formatCount(products.data.total)} products</span>
                <div className="flex gap-2">
                  <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button>
                  <Button variant="secondary" disabled={page * PAGE_SIZE >= products.data.total} onClick={() => setPage((value) => value + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
      <Card className="p-5">
        <h2 className="font-semibold">Create an order</h2>
        <p className="mt-1 text-sm text-slate-500">Prices and taxes are confirmed by the server using your dealer pricing.</p>
        {products.isError && <ErrorState message={products.error.message} />}
        {products.data?.items.length ? (
          <form
            className="mt-4 grid gap-3 sm:grid-cols-[1fr_10rem_auto]"
            onSubmit={(event) => {
              event.preventDefault();
              if (!productId || Number(quantity) <= 0) return;
              setCart((items) => [...items, { product_id: Number(productId), quantity }]);
              setProductId("");
              setQuantity("1");
            }}
          >
            <label className="text-sm font-medium text-slate-700">
              Product
              <select className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" value={productId} onChange={(event) => setProductId(event.target.value)} required>
                <option value="">Select a product</option>
                {products.data.items.map((product) => <option key={product.product_id} value={product.product_id}>{product.name} · {formatINR(product.price)}</option>)}
              </select>
            </label>
            <Field label="Quantity" type="number" min="0.001" step="0.001" value={quantity} onChange={(event) => setQuantity(event.target.value)} required />
            <Button className="self-end" type="submit"><Plus className="h-4 w-4" />Add item</Button>
          </form>
        ) : null}
        {cart.length > 0 && (
          <div className="mt-4 space-y-3">
            <ul className="divide-y divide-ivory-200 rounded-lg border border-ivory-200">
              {cart.map((line, index) => {
                const product = products.data?.items.find((item) => item.product_id === line.product_id);
                return (
                  <li className="flex items-center justify-between gap-3 px-3 py-2 text-sm" key={`${line.product_id}-${index}`}>
                    <span>{product?.name ?? `Product ${line.product_id}`} · {line.quantity}</span>
                    <Button type="button" variant="ghost" aria-label={`Remove ${product?.name ?? "item"}`} onClick={() => setCart((items) => items.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-4 w-4" /></Button>
                  </li>
                );
              })}
            </ul>
            {createOrder.isError && <ErrorState message={createOrder.error.message} />}
            <Button loading={createOrder.isPending} onClick={() => createOrder.mutate()}>Submit order</Button>
          </div>
        )}
      </Card>
      <Card className="overflow-hidden">
        <div className="border-b border-ivory-200 px-5 py-4"><h2 className="font-semibold">Order status</h2></div>
        {orders.isLoading && <LoadingState />}
        {orders.isError && <ErrorState message={orders.error.message} onRetry={() => void orders.refetch()} />}
        {orders.data && <DealerOrderHistory orders={orders.data.items} />}
        {orders.data && orders.data.total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>Page {orderPage} · {formatCount(orders.data.total)} orders</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={orderPage <= 1} onClick={() => setOrderPage((value) => value - 1)} aria-label="Previous orders page"><ChevronLeft className="h-4 w-4" /></Button>
              <Button variant="secondary" disabled={orderPage * PAGE_SIZE >= orders.data.total} onClick={() => setOrderPage((value) => value + 1)} aria-label="Next orders page"><ChevronRight className="h-4 w-4" /></Button>
            </div>
          </div>
        )}
      </Card>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ivory-200 px-5 py-4">
          <h2 className="font-semibold">Statement</h2>
          {statement.data && <span className="text-sm text-slate-600">Closing balance {formatINR(statement.data.closing_balance)}</span>}
        </div>
        {statement.isLoading && <LoadingState />}
        {statement.isError && <ErrorState message={statement.error.message} onRetry={() => void statement.refetch()} />}
        {statement.data?.items.length === 0 && <EmptyState title="No statement activity" />}
        {statement.data && statement.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase text-slate-500"><tr>{["Date", "Type", "Reference", "Debit", "Credit", "Balance"].map((item) => <th className="px-4 py-3" key={item}>{item}</th>)}</tr></thead>
              <tbody className="divide-y divide-ivory-200">
                {statement.data.items.map((item) => <tr key={`${item.type}-${item.id}`}><td className="px-4 py-3">{item.date}</td><td className="px-4 py-3 capitalize">{item.type.replace(/_/g, " ")}</td><td className="px-4 py-3">{item.reference}</td><td className="px-4 py-3 tabular-nums">{Number(item.debit) ? formatINR(item.debit) : "—"}</td><td className="px-4 py-3 tabular-nums">{Number(item.credit) ? formatINR(item.credit) : "—"}</td><td className="px-4 py-3 tabular-nums">{formatINR(item.balance)}</td></tr>)}
              </tbody>
            </table>
          </div>
        )}
        {statement.data && statement.data.total > 50 && (
          <div className="flex items-center justify-between border-t border-ivory-200 px-4 py-3 text-sm text-slate-600">
            <span>Page {statementPage} · {formatCount(statement.data.total)} transactions</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={statementPage <= 1} onClick={() => setStatementPage((value) => value - 1)} aria-label="Previous statement page"><ChevronLeft className="h-4 w-4" /></Button>
              <Button variant="secondary" disabled={statementPage * 50 >= statement.data.total} onClick={() => setStatementPage((value) => value + 1)} aria-label="Next statement page"><ChevronRight className="h-4 w-4" /></Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
