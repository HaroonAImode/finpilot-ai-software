import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { PageHeader, SearchField, StatusBadge } from "@/components/app-shell";
import { ChartCard, DonutChart, RevenueExpenseChart } from "@/components/charts";
import {
  approveExpense, createExpense, expenseCategoryBreakdown, expenseOptions, expenseSummary,
  listExpenses, rejectExpense, revenueVsExpensesChart,
  type Expense, type ExpenseStatus, type PaymentMethod,
} from "@/lib/transactions-service";

export const Route = createFileRoute("/app/expenses")({
  head: () => ({
    meta: [
      { title: "Expenses — FinPilot AI" },
      { name: "description", content: "Track, categorise and approve every business expense in one ledger." },
      { property: "og:title", content: "Expenses — FinPilot AI" },
      { property: "og:description", content: "Categorised expense ledger with approvals." },
    ],
  }),
  component: ExpensesPage,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

const STATUS_LABELS: Record<ExpenseStatus, string> = {
  pending: "Needs Review", approved: "Processed", rejected: "Rejected",
};

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "pending", label: "Pending Approval" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
] as const;

function AddExpenseDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    category: "", vendor_name: "", date: new Date().toISOString().slice(0, 10),
    amount_pkr: "", payment_method: "", reference_id: "",
  });

  const optionsQuery = useQuery({
    queryKey: ["expense-options"],
    queryFn: expenseOptions,
    // Suggestions only, fetched once when the dialog first opens.
    enabled: open,
  });

  const createMutation = useMutation({
    mutationFn: createExpense,
    onSuccess: () => {
      toast.success("Expense added — awaiting approval");
      queryClient.invalidateQueries({ queryKey: ["expenses"] });
      queryClient.invalidateQueries({ queryKey: ["expense-summary"] });
      setOpen(false);
      setForm({
        category: "", vendor_name: "", date: new Date().toISOString().slice(0, 10),
        amount_pkr: "", payment_method: "", reference_id: "",
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> Add Expense
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Expense</DialogTitle>
          <DialogDescription>Starts pending — approve or reject it from the ledger below.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>Category</Label>
            <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
              <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select a category" /></SelectTrigger>
              <SelectContent>
                {(optionsQuery.data?.categories ?? []).map((c) => (
                  <SelectItem key={c} value={c}>{c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Vendor / Payee</Label>
            <Input
              value={form.vendor_name}
              onChange={(e) => setForm({ ...form, vendor_name: e.target.value })}
              placeholder="e.g. Sindh Fuels Ltd."
              className="rounded-xl"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Date</Label>
              <Input
                type="date" value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Amount (PKR)</Label>
              <Input
                type="number" value={form.amount_pkr}
                onChange={(e) => setForm({ ...form, amount_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Payment Method</Label>
            <Select value={form.payment_method} onValueChange={(v) => setForm({ ...form, payment_method: v })}>
              <SelectTrigger className="rounded-xl"><SelectValue placeholder="Optional" /></SelectTrigger>
              <SelectContent>
                {(optionsQuery.data?.payment_methods ?? []).map((m) => (
                  <SelectItem key={m} value={m}>{m.replace("_", " ")}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Reference (optional)</Label>
            <Input
              value={form.reference_id}
              onChange={(e) => setForm({ ...form, reference_id: e.target.value })}
              className="rounded-xl"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.category.trim() || !form.amount_pkr || createMutation.isPending}
            onClick={() =>
              createMutation.mutate({
                category: form.category, vendor_name: form.vendor_name || null, date: form.date,
                amount_pkr: Number(form.amount_pkr),
                payment_method: (form.payment_method || null) as PaymentMethod | null,
                reference_id: form.reference_id || null,
              })
            }
          >
            {createMutation.isPending ? "Adding…" : "Add Expense"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ApprovalActions({ expense }: { expense: Expense }) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["expenses"] });
    queryClient.invalidateQueries({ queryKey: ["expense-summary"] });
    queryClient.invalidateQueries({ queryKey: ["expense-categories"] });
  };
  const approveMutation = useMutation({
    mutationFn: () => approveExpense(expense.id),
    onSuccess: () => { toast.success("Expense approved"); invalidate(); },
    onError: (error: Error) => toast.error(error.message),
  });
  const rejectMutation = useMutation({
    mutationFn: () => rejectExpense(expense.id),
    onSuccess: () => { toast.success("Expense rejected"); invalidate(); },
    onError: (error: Error) => toast.error(error.message),
  });

  if (expense.status !== "pending") return null;
  return (
    <div className="flex items-center justify-end gap-1">
      <Button
        size="icon" variant="ghost" className="h-7 w-7 text-success hover:text-success"
        disabled={approveMutation.isPending || rejectMutation.isPending}
        onClick={() => approveMutation.mutate()}
        title="Approve"
      >
        <Check className="h-4 w-4" />
      </Button>
      <Button
        size="icon" variant="ghost" className="h-7 w-7 text-destructive hover:text-destructive"
        disabled={approveMutation.isPending || rejectMutation.isPending}
        onClick={() => rejectMutation.mutate()}
        title="Reject"
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

function ExpensesPage() {
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]["value"]>("all");
  const [search, setSearch] = useState("");

  const summaryQuery = useQuery({ queryKey: ["expense-summary"], queryFn: expenseSummary });
  const categoriesQuery = useQuery({ queryKey: ["expense-categories"], queryFn: expenseCategoryBreakdown });
  const trendQuery = useQuery({ queryKey: ["revenue-vs-expenses"], queryFn: revenueVsExpensesChart });
  const expensesQuery = useQuery({
    queryKey: ["expenses", statusFilter, search],
    queryFn: () =>
      listExpenses({
        ...(statusFilter !== "all" ? { status: statusFilter } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
        limit: 100,
      }),
  });

  const summaryCards = summaryQuery.data
    ? [
        { label: "Total Expenses", value: summaryQuery.data.total, note: "This month" },
        { label: "Approved", value: summaryQuery.data.approved, note: "Booked spend" },
        {
          label: "Pending Approval", value: summaryQuery.data.pending,
          note: `${summaryQuery.data.pending_count} request${summaryQuery.data.pending_count === 1 ? "" : "s"}`,
        },
        {
          label: "Rejected", value: summaryQuery.data.rejected,
          note: `${summaryQuery.data.rejected_count} request${summaryQuery.data.rejected_count === 1 ? "" : "s"}`,
        },
      ]
    : [];

  return (
    <>
      <PageHeader
        title="Expenses"
        subtitle="Every outflow — typed in directly, or booked automatically when a purchase invoice is sent to accounting."
        actions={
          <>
            <SearchField
              placeholder="Search by vendor…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}>
              <SelectTrigger className="w-44 rounded-xl"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STATUS_FILTERS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <AddExpenseDialog />
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {summaryQuery.isLoading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)
          : summaryCards.map((s) => (
              <div key={s.label} className="surface lift p-5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{s.label}</p>
                <p className="mt-2 font-display text-2xl font-bold">{money(s.value)}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.note}</p>
              </div>
            ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ChartCard title="Expenses vs Revenue" subtitle="Trailing 7 months" className="lg:col-span-2">
          <RevenueExpenseChart data={trendQuery.data ?? []} />
        </ChartCard>
        <ChartCard title="Category Split" subtitle="This month, approved only">
          <DonutChart data={categoriesQuery.data ?? []} />
        </ChartCard>
      </div>

      <section className="surface mt-6 overflow-hidden">
        <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-5 py-4">
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold">Expense Ledger</h3>
            <p className="text-xs text-muted-foreground">
              {expensesQuery.data ? `${expensesQuery.data.total} records` : "Loading…"}
            </p>
          </div>
          <Button variant="ghost" size="sm" className="gap-2">
            <Download className="h-4 w-4" /> Export
          </Button>
        </header>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead>Vendor</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Method</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {expensesQuery.isLoading && (
                <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">Loading…</TableCell></TableRow>
              )}
              {expensesQuery.data?.expenses.length === 0 && (
                <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">No expenses match this filter.</TableCell></TableRow>
              )}
              {expensesQuery.data?.expenses.map((e) => (
                <TableRow key={e.id} className="hover:bg-muted/50">
                  <TableCell className="font-medium">{e.category}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {e.vendor_name ?? "—"}
                    {e.source === "invoice" && (
                      <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                        From invoice
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{e.date}</TableCell>
                  <TableCell className="text-muted-foreground">{e.payment_method?.replace("_", " ") ?? "—"}</TableCell>
                  <TableCell className="text-right font-medium">{money(e.amount_pkr)}</TableCell>
                  <TableCell>
                    <StatusBadge status={STATUS_LABELS[e.status]} />
                  </TableCell>
                  <TableCell><ApprovalActions expense={e} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
    </>
  );
}
