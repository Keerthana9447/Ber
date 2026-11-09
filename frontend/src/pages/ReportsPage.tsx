import { useState } from "react";
import { Button, Card, Field, useToast } from "../components/ui";
import { apiBlob } from "../lib/api";

const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
const monthStart = `${today.slice(0, 7)}-01`;

const reports = [
  { key: "trial-balance", name: "Trial balance", description: "Account debit and credit balances for the selected period." },
  { key: "profit-and-loss", name: "Profit and loss", description: "Revenue, cost, expenses, and profit before income tax." },
  { key: "cash-book", name: "Cash book", description: "Cash activity with opening and closing balances." },
  { key: "bank-book", name: "Bank book", description: "Bank activity with opening and closing balances." },
  { key: "sales", name: "Sales register", description: "Issued sales grouped by date for the selected period." },
  { key: "purchase-register", name: "Purchase register", description: "Posted supplier invoices and tax totals." },
  { key: "input-gst", name: "Input GST summary", description: "Recoverable input-tax totals by configured GST rate." },
  { key: "stock-valuation", name: "Stock valuation", description: "On-hand quantities and valued inventory balances." },
] as const;

type ExportFormat = "csv" | "xlsx" | "pdf";

export function ReportsPage() {
  const toast = useToast();
  const [from, setFrom] = useState(monthStart);
  const [to, setTo] = useState(today);
  const [activeExport, setActiveExport] = useState<string | null>(null);

  async function exportReport(reportKey: string, format: ExportFormat) {
    const exportKey = `${reportKey}.${format}`;
    setActiveExport(exportKey);
    try {
      const query = new URLSearchParams();
      if (from) query.set("from", from);
      if (to) query.set("to", to);
      const suffix = query.size ? `?${query.toString()}` : "";
      const blob = await apiBlob(`/reports/export/${reportKey}.${format}${suffix}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${reportKey}.${format === "xlsx" ? "xlsx" : format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Report export failed.");
    } finally {
      setActiveExport(null);
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-forest-900">Reports</h1>
        <p className="text-sm text-slate-500">Export current server-calculated reports. Every download is permission-checked and audit-logged.</p>
      </header>
      <Card className="grid gap-4 p-5 sm:grid-cols-2">
        <Field label="From" type="date" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} />
        <Field label="To" type="date" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {reports.map((report) => (
          <Card key={report.key} className="flex flex-wrap items-center justify-between gap-4 p-5">
            <div className="min-w-[14rem] flex-1">
              <h2 className="font-semibold text-forest-900">{report.name}</h2>
              <p className="mt-1 text-sm text-slate-500">{report.description}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {(["csv", "xlsx", "pdf"] as const).map((format) => {
                const exportKey = `${report.key}.${format}`;
                return (
                  <Button
                    key={format}
                    variant="secondary"
                    disabled={activeExport !== null && activeExport !== exportKey}
                    loading={activeExport === exportKey}
                    onClick={() => void exportReport(report.key, format)}
                  >
                    {format.toUpperCase()}
                  </Button>
                );
              })}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
