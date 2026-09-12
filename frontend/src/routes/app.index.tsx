import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Banknote,
  CalendarDays,
  CheckCircle2,
  Clock,
  Landmark,
  PiggyBank,
  Receipt,
  TrendingUp,
  Upload,
  Users,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader, StatusBadge } from "@/components/app-shell";
import {
  CashFlowChart,
  ChartCard,
  DonutChart,
  HorizontalBars,
  PieChartSimple,
  RevenueExpenseChart,
  VerticalBars,
} from "@/components/charts";
import { useAuth } from "@/lib/auth-context";
import { getInvoiceStatusSummary, getSalesSummary, listInvoices } from "@/lib/invoice-service";
import { topVendors } from "@/lib/vendors-service";
import {
  expenseCategoryBreakdown, listExpenses, revenueVsExpensesChart, cashFlowChart, transactionsKpis,
} from "@/lib/transactions-service";

export const Route = createFileRoute("/app/")({
  head: () => ({
    meta: [
      { title: "Dashboard — FinPilot AI" },
      { name: "description", content: "Live revenue, expense, payroll and cash-flow overview for your business." },
      { property: "og:title", content: "Dashboard — FinPilot AI" },
      { property: "og:description", content: "Live revenue, expense, payroll and cash-flow overview." },
    ],
  }),
  component: Dashboard,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

const icons = {
  wallet: Wallet,
  trending: TrendingUp,
  receipt: Receipt,
  piggy: PiggyBank,
  clock: Clock,
  check: CheckCircle2,
  users: Users,
  bank: Landmark,
} as const;

interface KpiDef {
  label: string;
  value: number;
  hint: string;
  icon: keyof typeof icons;
  raw?: boolean;
  unavailable?: boolean;
}

/** No historical baseline is stored anywhere yet, so unlike the old dummy
 *  data this card deliberately has no vs-last-month delta arrow — a
 *  fabricated trend would be worse than none. */
function KpiCard({ kpi }: { kpi: KpiDef }) {
  const Icon = icons[kpi.icon];
  return (
    <div className="surface lift p-5">
      <div className="flex items-start justify-between gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
          <Icon className="h-4.5 w-4.5" />
        </span>
        {kpi.unavailable && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-[11px] font-medium text-muted-foreground"
            title="Invoice Service could not be reached"
          >
            <AlertTriangle className="h-3 w-3" /> Unavailable
          </span>
        )}
      </div>
      <p className="mt-4 truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">{kpi.label}</p>
      <p className="mt-1 font-display text-2xl font-bold tracking-tight">
        {kpi.raw ? kpi.value.toLocaleString() : money(kpi.value)}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{kpi.hint}</p>
    </div>
  );
}

function Dashboard() {
  const { user } = useAuth();
  const today = new Date();
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), 1).getDay();
  const firstName = user?.full_name?.split(" ")[0];

  const kpisQuery = useQuery({ queryKey: ["transactions-kpis"], queryFn: transactionsKpis });
  const trendQuery = useQuery({ queryKey: ["revenue-vs-expenses"], queryFn: revenueVsExpensesChart });
  const cashFlowQuery = useQuery({ queryKey: ["cash-flow"], queryFn: cashFlowChart });
  const categoriesQuery = useQuery({ queryKey: ["expense-categories"], queryFn: expenseCategoryBreakdown });
  const salesSummaryQuery = useQuery({ queryKey: ["sales-summary"], queryFn: getSalesSummary });
  const topVendorsQuery = useQuery({ queryKey: ["vendors", "top"], queryFn: () => topVendors(5) });
  const statusSummaryQuery = useQuery({ queryKey: ["invoice-status-summary"], queryFn: getInvoiceStatusSummary });
  const recentInvoicesQuery = useQuery({
    queryKey: ["invoices", "recent"], queryFn: () => listInvoices({ limit: 7 }),
  });
  const pendingApprovalsQuery = useQuery({
    queryKey: ["expenses", "pending-approvals"],
    queryFn: () => listExpenses({ status: "pending", limit: 5 }),
  });

  const k = kpisQuery.data;
  const kpiDefs: KpiDef[] = k
    ? [
        { label: "Today's Revenue", value: k.today_revenue, hint: "scanned sales, today", icon: "wallet", unavailable: k.revenue_unavailable },
        { label: "Monthly Revenue", value: k.monthly_revenue, hint: "this month", icon: "trending", unavailable: k.revenue_unavailable },
        { label: "Monthly Expenses", value: k.monthly_expenses, hint: "approved, this month", icon: "receipt" },
        { label: "Net Profit", value: k.net_profit, hint: "revenue − expenses", icon: "piggy", unavailable: k.revenue_unavailable },
        { label: "Pending Invoices", value: k.pending_invoices, hint: "awaiting review", icon: "clock", raw: true, unavailable: k.revenue_unavailable },
        { label: "Processed Invoices", value: k.processed_invoices, hint: "this ledger", icon: "check", raw: true, unavailable: k.revenue_unavailable },
        { label: "Employee Salaries", value: k.employee_salaries, hint: "\"Salaries\" category, this month", icon: "users" },
        { label: "Cash Balance", value: k.cash_balance, hint: "cumulative, approximate", icon: "bank", unavailable: k.revenue_unavailable },
      ]
    : [];

  const topCustomers = (salesSummaryQuery.data?.top_customers ?? []).map((c) => ({ name: c.customer_name, value: c.total }));
  const topVendorsChart = (topVendorsQuery.data ?? [])
    .filter((v) => !v.spend_unavailable && v.total_spend_pkr > 0)
    .map((v) => ({ name: v.name, value: v.total_spend_pkr }));

  const counts = statusSummaryQuery.data?.counts;
  const invoiceStatusChart = counts
    ? [
        { name: "Processed", value: counts.processed + counts.validated + counts.sent_to_accounting },
        { name: "Needs Review", value: counts.needs_review + counts.needs_review_high_priority },
      ].filter((s) => s.value > 0)
    : [];

  return (
    <>
      <PageHeader
        title={firstName ? `Good to see you, ${firstName}` : "Good to see you"}
        subtitle={`Here's what happened across your books today — ${today.toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" })}.`}
        actions={
          <>
            <Button variant="outline" className="rounded-xl">
              Export
            </Button>
            <Button asChild className="gap-2 rounded-xl">
              <Link to="/app/scanner">
                <Upload className="h-4 w-4" /> Upload Invoice
              </Link>
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpisQuery.isLoading
          ? Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-32 rounded-2xl" />)
          : kpiDefs.map((kpi) => <KpiCard key={kpi.label} kpi={kpi} />)}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ChartCard
          title="Monthly Revenue vs Expenses"
          subtitle="Last 7 months (PKR)"
          className="lg:col-span-2"
        >
          <RevenueExpenseChart data={trendQuery.data ?? []} />
        </ChartCard>
        <ChartCard title="Expense Categories" subtitle="This month, approved only">
          <DonutChart data={categoriesQuery.data ?? []} />
        </ChartCard>
        <ChartCard title="Top Customers" subtitle="From scanned sales — see Revenue Manager">
          <VerticalBars data={topCustomers} />
        </ChartCard>
        <ChartCard title="Cash Flow" subtitle="Inflow vs outflow" className="lg:col-span-2">
          <CashFlowChart data={cashFlowQuery.data ?? []} />
        </ChartCard>
        <ChartCard title="Top Vendors" subtitle="Spend, derived from Invoice Service" className="lg:col-span-2">
          <HorizontalBars data={topVendorsChart} />
        </ChartCard>
        <ChartCard title="Invoice Status" subtitle={`${statusSummaryQuery.data?.total ?? 0} purchase documents`}>
          <PieChartSimple data={invoiceStatusChart} />
        </ChartCard>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className="surface overflow-hidden xl:col-span-2">
          <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-5 py-4">
            <div className="min-w-0">
              <h3 className="truncate text-base font-semibold">Recent Uploaded Invoices</h3>
              <p className="text-xs text-muted-foreground">Auto-extracted by FinPilot AI</p>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/app/scanner">View all</Link>
            </Button>
          </header>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {/* Explicit widths on the short-content columns — an HTML
                   * table's default layout otherwise stretches every column
                   * to fill the container, leaving large, empty-looking gaps
                   * (the same issue found and fixed on the Scanner page's
                   * own list). */}
                  <TableHead className="w-40">Invoice No</TableHead>
                  <TableHead>Vendor</TableHead>
                  <TableHead className="w-28">Date</TableHead>
                  <TableHead className="w-32 text-right">Amount</TableHead>
                  <TableHead className="w-32">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentInvoicesQuery.data?.invoices.length === 0 && (
                  <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">No invoices scanned yet.</TableCell></TableRow>
                )}
                {recentInvoicesQuery.data?.invoices.map((inv) => (
                  <TableRow key={inv.id} className="transition-colors hover:bg-muted/50">
                    <TableCell className="font-medium">{inv.invoice_number ?? "—"}</TableCell>
                    <TableCell>{inv.vendor_name ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{inv.invoice_date ?? "—"}</TableCell>
                    <TableCell className="text-right font-medium">{inv.total !== null ? money(inv.total) : "—"}</TableCell>
                    <TableCell>
                      <StatusBadge status={inv.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <div className="flex flex-col gap-4">
          <section className="surface p-5">
            <div className="flex items-center gap-2">
              <CalendarDays className="h-4 w-4 text-primary" />
              <h3 className="text-base font-semibold">
                {today.toLocaleDateString("en-US", { month: "long", year: "numeric" })}
              </h3>
            </div>
            <div className="mt-4 grid grid-cols-7 gap-1 text-center text-[11px] text-muted-foreground">
              {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
                <span key={i}>{d}</span>
              ))}
              {Array.from({ length: firstDay }).map((_, i) => (
                <span key={`e${i}`} />
              ))}
              {Array.from({ length: daysInMonth }).map((_, i) => {
                const day = i + 1;
                const isToday = day === today.getDate();
                return (
                  <span
                    key={day}
                    className={`grid h-8 place-items-center rounded-lg text-xs ${
                      isToday
                        ? "bg-[image:var(--gradient-brand)] font-semibold text-primary-foreground"
                        : "text-foreground/80"
                    }`}
                  >
                    {day}
                  </span>
                );
              })}
            </div>
          </section>

          <section className="surface p-5">
            <div className="flex items-center gap-2">
              <Banknote className="h-4 w-4 text-primary" />
              <h3 className="text-base font-semibold">Pending Approvals</h3>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">Expenses awaiting a decision — see the Expenses page</p>
            <ul className="mt-4 space-y-3">
              {pendingApprovalsQuery.data?.expenses.length === 0 && (
                <li className="rounded-xl bg-muted/50 px-3 py-2.5 text-center text-xs text-muted-foreground">
                  Nothing pending right now.
                </li>
              )}
              {pendingApprovalsQuery.data?.expenses.map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-3 rounded-xl bg-muted/50 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{e.vendor_name ?? e.category}</p>
                    <p className="text-xs text-muted-foreground">{e.category} · {e.date}</p>
                  </div>
                  <span className="shrink-0 text-sm font-semibold">{money(e.amount_pkr)}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
