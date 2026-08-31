import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/app-shell";
import {
  CashFlowChart,
  ChartCard,
  DonutChart,
  HorizontalBars,
  MarginChart,
  PieChartSimple,
  RevenueExpenseChart,
  VerticalBars,
} from "@/components/charts";
import { getInvoiceStatusSummary, getSalesSummary } from "@/lib/invoice-service";
import { topVendors } from "@/lib/vendors-service";
import { cashFlowChart, expenseCategoryBreakdown, revenueVsExpensesChart } from "@/lib/transactions-service";
import { listEmployees } from "@/lib/hr-service";
import { procurementStats } from "@/lib/procurement-service";

export const Route = createFileRoute("/app/analytics")({
  head: () => ({
    meta: [
      { title: "Analytics — FinPilot AI" },
      { name: "description", content: "Deep financial analytics: growth, margins, cash flow and revenue forecasts." },
      { property: "og:title", content: "Analytics — FinPilot AI" },
      { property: "og:description", content: "Growth, margin, cash flow and forecast analytics." },
    ],
  }),
  component: AnalyticsPage,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

function pctChange(current: number, previous: number): number {
  if (previous === 0) return 0;
  return ((current - previous) / Math.abs(previous)) * 100;
}

function StatCard({ label, value, delta, note }: { label: string; value: string; delta: number; note: string }) {
  const up = delta >= 0;
  return (
    <div className="surface lift p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="mt-2 flex items-end justify-between gap-2">
        <span className="font-display text-2xl font-bold">{value}</span>
        <span
          className={`inline-flex items-center gap-0.5 rounded-full px-2 py-1 text-[11px] font-semibold ${
            up ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive"
          }`}
        >
          {up ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
          {Math.abs(delta).toFixed(1)}%
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{note}</p>
    </div>
  );
}

function AnalyticsPage() {
  const trendQuery = useQuery({ queryKey: ["revenue-vs-expenses"], queryFn: revenueVsExpensesChart });
  const cashFlowQuery = useQuery({ queryKey: ["cash-flow"], queryFn: cashFlowChart });
  const categoriesQuery = useQuery({ queryKey: ["expense-categories"], queryFn: expenseCategoryBreakdown });
  const salesSummaryQuery = useQuery({ queryKey: ["sales-summary"], queryFn: getSalesSummary });
  const topVendorsQuery = useQuery({ queryKey: ["vendors", "top"], queryFn: () => topVendors(5) });
  const statusSummaryQuery = useQuery({ queryKey: ["invoice-status-summary"], queryFn: getInvoiceStatusSummary });
  const employeesQuery = useQuery({ queryKey: ["employees", "all"], queryFn: () => listEmployees({ limit: 200 }) });
  const procurementQuery = useQuery({ queryKey: ["procurement-stats"], queryFn: procurementStats });

  const trend = trendQuery.data ?? [];
  const flow = cashFlowQuery.data ?? [];
  const current = trend.at(-1);
  const previous = trend.at(-2);
  const currentFlow = flow.at(-1);
  const previousFlow = flow.at(-2);

  const revenueGrowth = current && previous ? pctChange(current.revenue, previous.revenue) : 0;
  const expenseGrowth = current && previous ? pctChange(current.expenses, previous.expenses) : 0;
  const currentMargin = current && current.revenue ? ((current.revenue - current.expenses) / current.revenue) * 100 : 0;
  const previousMargin = previous && previous.revenue ? ((previous.revenue - previous.expenses) / previous.revenue) * 100 : 0;
  const netCashFlow = currentFlow ? currentFlow.inflow - currentFlow.outflow : 0;
  const previousNetCashFlow = previousFlow ? previousFlow.inflow - previousFlow.outflow : 0;

  const marginTrend = trend.map((t) => ({
    month: t.month, margin: t.revenue ? Math.round(((t.revenue - t.expenses) / t.revenue) * 1000) / 10 : 0,
  }));

  const topCustomers = (salesSummaryQuery.data?.top_customers ?? []).map((c) => ({ name: c.customer_name, value: c.total }));
  const topVendorsChart = (topVendorsQuery.data ?? [])
    .filter((v) => !v.spend_unavailable && v.total_spend_pkr > 0)
    .map((v) => ({ name: v.name, value: v.total_spend_pkr }));

  const byDepartment = Object.values(
    (employeesQuery.data?.employees ?? []).reduce<Record<string, { name: string; value: number }>>((acc, e) => {
      acc[e.department] = { name: e.department, value: (acc[e.department]?.value ?? 0) + e.salary_pkr + e.bonus_pkr };
      return acc;
    }, {}),
  ).sort((a, b) => b.value - a.value);

  const procurementPipeline = procurementQuery.data
    ? [
        { name: "Pending", value: procurementQuery.data.pending_count },
        { name: "Completed", value: procurementQuery.data.completed_count },
        { name: "Delayed", value: procurementQuery.data.delayed_count },
        { name: "Cancelled", value: procurementQuery.data.cancelled_count },
      ]
    : [];

  const counts = statusSummaryQuery.data?.counts;
  const invoiceStatusChart = counts
    ? [
        { name: "Processed", value: counts.processed + counts.validated + counts.sent_to_accounting },
        { name: "Needs Review", value: counts.needs_review + counts.needs_review_high_priority },
      ].filter((s) => s.value > 0)
    : [];

  const loadingStats = trendQuery.isLoading || cashFlowQuery.isLoading;

  return (
    <>
      <PageHeader
        title="Financial Analytics"
        subtitle="A complete picture of business performance, drawn live from every service."
        actions={
          <Button variant="outline" className="gap-2 rounded-xl">
            <Download className="h-4 w-4" /> Export dataset
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loadingStats ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-2xl" />)
        ) : (
          <>
            <StatCard
              label="Revenue Growth" value={`${revenueGrowth >= 0 ? "+" : ""}${revenueGrowth.toFixed(1)}%`}
              delta={revenueGrowth} note={`MoM, ${money(current?.revenue ?? 0)}`}
            />
            <StatCard
              label="Expense Growth" value={`${expenseGrowth >= 0 ? "+" : ""}${expenseGrowth.toFixed(1)}%`}
              delta={-expenseGrowth} note={`MoM, ${money(current?.expenses ?? 0)}`}
            />
            <StatCard
              label="Profit Margin" value={`${currentMargin.toFixed(1)}%`}
              delta={currentMargin - previousMargin} note={`vs ${previousMargin.toFixed(1)}% last month`}
            />
            <StatCard
              label="Cash Flow" value={money(netCashFlow)}
              delta={pctChange(netCashFlow, previousNetCashFlow)} note={netCashFlow >= 0 ? "Net positive" : "Net negative"}
            />
          </>
        )}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ChartCard title="Monthly Comparison" subtitle="Revenue vs expenses" className="lg:col-span-2">
          <RevenueExpenseChart data={trend} />
        </ChartCard>
        <ChartCard title="Profit Margin" subtitle="Trailing 7 months">
          <MarginChart data={marginTrend} />
        </ChartCard>
        <ChartCard title="Cash Flow" subtitle="Inflow vs outflow" className="lg:col-span-2">
          <CashFlowChart data={flow} />
        </ChartCard>
        <ChartCard title="Expense Categories" subtitle="This month, approved only">
          <DonutChart data={categoriesQuery.data ?? []} />
        </ChartCard>
        <ChartCard title="Top Customers" subtitle="From scanned sales">
          <HorizontalBars data={topCustomers} />
        </ChartCard>
        <ChartCard title="Top Vendors" subtitle="Spend contribution">
          <HorizontalBars data={topVendorsChart} />
        </ChartCard>
        <ChartCard title="Payroll by Department" subtitle="Gross cost">
          <VerticalBars data={byDepartment} />
        </ChartCard>
        <ChartCard title="Procurement Pipeline" subtitle="Requests & orders, all time" className="lg:col-span-2">
          <VerticalBars data={procurementPipeline} />
        </ChartCard>
        <ChartCard title="Invoice Status" subtitle="Document pipeline">
          <PieChartSimple data={invoiceStatusChart} />
        </ChartCard>
      </div>
    </>
  );
}
