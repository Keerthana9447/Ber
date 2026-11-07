import { useQuery } from "@tanstack/react-query";
import { api, isEndpointMissing } from "../lib/api";
import { Card, EmptyState, ErrorState, LoadingState, NotAvailableState } from "../components/ui";

const label = (key: string) => key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function SettingsPage() {
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => api<Record<string, unknown>>("/settings", { signal }),
    retry: false,
  });
  const entries = Object.entries(query.data ?? {}).filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v));
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-forest-900">Settings</h1>
        <p className="text-sm text-slate-500">Company profile, tax identity and invoice configuration (read-only view).</p>
      </div>
      <Card>
        {query.isLoading && <LoadingState />}
        {query.isError &&
          (isEndpointMissing(query.error) ? (
            <NotAvailableState endpoint="/settings" label="Settings" />
          ) : (
            <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
          ))}
        {query.isSuccess && entries.length === 0 && <EmptyState title="No company settings saved yet" hint="Official details such as GSTIN, PAN and bank accounts must be entered by an administrator." />}
        {query.isSuccess && entries.length > 0 && (
          <dl className="divide-y divide-ivory-200">
            {entries.map(([k, v]) => (
              <div key={k} className="grid grid-cols-3 gap-4 px-4 py-3 text-sm">
                <dt className="text-slate-500">{label(k)}</dt>
                <dd className="col-span-2 break-words text-slate-900">{v === null || v === "" ? "—" : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </div>
  );
}
