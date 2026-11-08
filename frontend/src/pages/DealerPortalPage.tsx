import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { formatCount, formatINR } from "../lib/format";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState } from "../components/ui";

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
const PAGE_SIZE = 20;

export function DealerPortalPage() {
  const [page, setPage] = useState(1);
  const profile = useQuery({
    queryKey: ["dealer-portal", "profile"],
    queryFn: ({ signal }) => api<DealerProfile>("/dealer/portal/profile", { signal }),
    retry: false,
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
    </div>
  );
}
