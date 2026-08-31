import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  BarChart3,
  FileSpreadsheet,
  FileText,
  Landmark,
  Percent,
  Receipt,
  ShoppingCart,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/app-shell";
import {
  createBalanceSheet, createCashFlow, createProfitLoss, createPurchaseReport, createSalesReport,
  createTaxSummary, fetchReportExcel, fetchReportPdf,
  type Report,
} from "@/lib/reports-service";

export const Route = createFileRoute("/app/reports")({
  head: () => ({
    meta: [
      { title: "Reports — FinPilot AI" },
      { name: "description", content: "Generate P&L, cash flow, tax, sales and purchase reports instantly." },
      { property: "og:title", content: "Reports — FinPilot AI" },
      { property: "og:description", content: "One-click financial reports in PDF and Excel." },
    ],
  }),
  component: ReportsPage,
});

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function firstOfMonthIso(): string {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}

async function downloadBlob(fetcher: () => Promise<Blob>, filename: string) {
  const blob = await fetcher();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

interface ReportDef {
  key: string;
  title: string;
  desc: string;
  icon: typeof BarChart3;
  /** Balance Sheet is a point-in-time snapshot (architecture report
   *  §5.9's own wording); every other report is a period range. */
  kind: "period" | "point_in_time";
  generate: (start: string, end: string) => Promise<Report>;
}

const REPORTS: ReportDef[] = [
  {
    key: "profit_loss", title: "Profit & Loss", desc: "Income, expenses and net profit for any period.",
    icon: BarChart3, kind: "period", generate: createProfitLoss,
  },
  {
    key: "balance_sheet", title: "Balance Sheet", desc: "A simplified cash-position snapshot — see the report's own notes for what it doesn't cover.",
    icon: Landmark, kind: "point_in_time", generate: (_start, end) => createBalanceSheet(end),
  },
  {
    key: "cash_flow", title: "Cash Flow", desc: "Inflow vs outflow, month by month, for the period.",
    icon: Receipt, kind: "period", generate: createCashFlow,
  },
  {
    key: "tax_summary", title: "Tax Summary", desc: "A simplified GST/withholding estimate from your configured rates.",
    icon: Percent, kind: "period", generate: createTaxSummary,
  },
  {
    key: "sales", title: "Sales Report", desc: "Revenue by customer for the period.",
    icon: FileText, kind: "period", generate: createSalesReport,
  },
  {
    key: "purchases", title: "Purchase Report", desc: "Vendor spend for the period.",
    icon: ShoppingCart, kind: "period", generate: createPurchaseReport,
  },
];

function ReportCard({ def }: { def: ReportDef }) {
  const [periodStart, setPeriodStart] = useState(firstOfMonthIso());
  const [periodEnd, setPeriodEnd] = useState(todayIso());
  const [report, setReport] = useState<Report | null>(null);
  const [downloading, setDownloading] = useState<"pdf" | "excel" | null>(null);

  const generateMutation = useMutation({
    mutationFn: () => def.generate(periodStart, periodEnd),
    onSuccess: (result) => {
      setReport(result);
      toast.success(`${def.title} generated`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  async function handleDownload(format: "pdf" | "excel") {
    if (!report) return;
    setDownloading(format);
    try {
      const filename = `${def.key}-${report.period_end}.${format === "pdf" ? "pdf" : "xlsx"}`;
      await downloadBlob(() => (format === "pdf" ? fetchReportPdf(report.id) : fetchReportExcel(report.id)), filename);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Download failed");
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="surface lift flex flex-col p-6">
      <span className="grid h-11 w-11 place-items-center rounded-xl bg-[image:var(--gradient-soft)] text-primary">
        <def.icon className="h-5 w-5" />
      </span>
      <h3 className="mt-4 font-display text-lg font-semibold">{def.title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{def.desc}</p>

      <div className={`mt-4 grid gap-2 ${def.kind === "period" ? "grid-cols-2" : "grid-cols-1"}`}>
        {def.kind === "period" && (
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} className="rounded-lg text-sm" />
          </div>
        )}
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">{def.kind === "period" ? "To" : "As of"}</Label>
          <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} className="rounded-lg text-sm" />
        </div>
      </div>

      {report && (
        <div className="mt-4 space-y-1 rounded-xl bg-muted/40 p-3">
          {report.payload.summary.slice(0, 3).map((line) => (
            <div key={line.label} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{line.label}</span>
              <span className="font-medium">{line.value}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <Button
          variant="outline" size="sm" className="gap-1.5 rounded-lg" disabled={!report || downloading !== null}
          onClick={() => handleDownload("pdf")}
        >
          <FileText className="h-3.5 w-3.5" /> {downloading === "pdf" ? "…" : "PDF"}
        </Button>
        <Button
          variant="outline" size="sm" className="gap-1.5 rounded-lg" disabled={!report || downloading !== null}
          onClick={() => handleDownload("excel")}
        >
          <FileSpreadsheet className="h-3.5 w-3.5" /> {downloading === "excel" ? "…" : "Excel"}
        </Button>
        <Button size="sm" className="rounded-lg" disabled={generateMutation.isPending} onClick={() => generateMutation.mutate()}>
          {generateMutation.isPending ? "Generating…" : report ? "Regenerate" : "Generate"}
        </Button>
      </div>
    </div>
  );
}

function ReportsPage() {
  return (
    <>
      <PageHeader
        title="Reports"
        subtitle="Statements generated live from your ledger — see each report's own notes for what it does and doesn't cover."
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {REPORTS.map((def) => <ReportCard key={def.key} def={def} />)}
      </div>
    </>
  );
}
