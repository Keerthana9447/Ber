import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

type Readiness = { ready: boolean; missing_fields: string[] };

export function InvoiceReadinessBanner() {
  const query = useQuery({
    queryKey: ["settings", "readiness"],
    queryFn: ({ signal }) => api<Readiness>("/settings/readiness", { signal }),
    retry: false,
  });

  if (query.isLoading || !query.data) {
    return <div role="status" className="rounded-lg border border-ivory-300 bg-white px-4 py-3 text-sm text-slate-600">Checking invoice readiness…</div>;
  }
  if (query.isError) {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
        Invoice readiness could not be checked. Invoice drafts are disabled until company settings can be verified.
        <button type="button" className="ml-2 underline" onClick={() => query.refetch()}>Retry</button>
      </div>
    );
  }
  if (query.data.ready) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-forest-200 bg-forest-50 px-4 py-3 text-sm text-forest-900">
        <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden /> Company settings are complete for tax invoice drafts.
      </div>
    );
  }
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gold-300 bg-gold-300/20 px-4 py-3 text-sm text-slate-800">
      <span className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-gold-600" aria-hidden />
        <span>Invoice drafts are disabled. Configure: {query.data.missing_fields.join(", ")}.</span>
      </span>
      <Link to="/settings" className="font-medium text-forest-800 underline">Open Settings</Link>
    </div>
  );
}
