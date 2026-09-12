import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote, Pencil, Plus, Trash2, Wallet } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { PageHeader, SearchField, StatusBadge } from "@/components/app-shell";
import { ChartCard, HorizontalBars } from "@/components/charts";
import {
  createEmployee, deleteEmployee, employeeOptions, listEmployees, payrollSummary, processPayroll,
  updateEmployee, type Employee, type EmployeeUpdatePayload,
} from "@/lib/hr-service";

export const Route = createFileRoute("/app/employees")({
  head: () => ({
    meta: [
      { title: "Employees & Payroll — FinPilot AI" },
      { name: "description", content: "Salaries, bonuses, deductions and payment status for your whole team." },
      { property: "og:title", content: "Employees & Payroll — FinPilot AI" },
      { property: "og:description", content: "Payroll dashboard with salary, bonus and deduction tracking." },
    ],
  }),
  component: EmployeesPage,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

const initials = (name: string) =>
  name.split(" ").map((n) => n[0]).join("");

const emptyEmployeeForm = () => ({
  name: "", department: "", role: "", salary_pkr: "", bonus_pkr: "", deductions_pkr: "",
  joining_date: new Date().toISOString().slice(0, 10),
});

function AddEmployeeDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyEmployeeForm());

  const optionsQuery = useQuery({
    queryKey: ["employee-options"], queryFn: employeeOptions,
    enabled: open,
  });

  const createMutation = useMutation({
    mutationFn: createEmployee,
    onSuccess: (employee) => {
      toast.success(`${employee.name} added`);
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["payroll-summary"] });
      setOpen(false);
      setForm(emptyEmployeeForm());
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> Add Employee
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Employee</DialogTitle>
          <DialogDescription>Joins the next payroll run automatically once active.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>Full Name</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-xl" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Department</Label>
              <Select value={form.department} onValueChange={(v) => setForm({ ...form, department: v })}>
                <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select a department" /></SelectTrigger>
                <SelectContent>
                  {(optionsQuery.data?.departments ?? []).map((d) => (
                    <SelectItem key={d} value={d}>{d}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Role</Label>
              <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select a role" /></SelectTrigger>
                <SelectContent>
                  {(optionsQuery.data?.roles ?? []).map((r) => (
                    <SelectItem key={r} value={r}>{r}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Joining Date</Label>
              <Input
                type="date" value={form.joining_date}
                onChange={(e) => setForm({ ...form, joining_date: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Salary (PKR)</Label>
              <Input
                type="number" value={form.salary_pkr}
                onChange={(e) => setForm({ ...form, salary_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Bonus (optional)</Label>
              <Input
                type="number" value={form.bonus_pkr}
                onChange={(e) => setForm({ ...form, bonus_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Deductions (optional)</Label>
              <Input
                type="number" value={form.deductions_pkr}
                onChange={(e) => setForm({ ...form, deductions_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.name.trim() || !form.department || !form.role || !form.salary_pkr || createMutation.isPending}
            onClick={() =>
              createMutation.mutate({
                name: form.name, department: form.department, role: form.role, salary_pkr: Number(form.salary_pkr),
                bonus_pkr: form.bonus_pkr ? Number(form.bonus_pkr) : 0,
                deductions_pkr: form.deductions_pkr ? Number(form.deductions_pkr) : 0,
                joining_date: form.joining_date,
              })
            }
          >
            {createMutation.isPending ? "Adding…" : "Add Employee"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function editFormFromEmployee(employee: Employee) {
  return {
    name: employee.name, department: employee.department, role: employee.role ?? "",
    salary_pkr: String(employee.salary_pkr), bonus_pkr: String(employee.bonus_pkr),
    deductions_pkr: String(employee.deductions_pkr), joining_date: employee.joining_date,
    active: employee.active,
  };
}

function EditEmployeeDialog({ employee }: { employee: Employee }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(() => editFormFromEmployee(employee));

  const optionsQuery = useQuery({
    queryKey: ["employee-options"], queryFn: employeeOptions,
    enabled: open,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: EmployeeUpdatePayload) => updateEmployee(employee.id, payload),
    onSuccess: (updated) => {
      toast.success(`${updated.name} updated`);
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["payroll-summary"] });
      setOpen(false);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  function handleOpenChange(next: boolean) {
    if (next) setForm(editFormFromEmployee(employee));
    setOpen(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant="ghost" size="icon"
          className="h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground"
        >
          <Pencil className="h-4 w-4" />
          <span className="sr-only">Edit {employee.name}</span>
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Employee</DialogTitle>
          <DialogDescription>Changes apply to the next payroll run onward.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>Full Name</Label>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-xl" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Department</Label>
              <Select value={form.department} onValueChange={(v) => setForm({ ...form, department: v })}>
                <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select a department" /></SelectTrigger>
                <SelectContent>
                  {(optionsQuery.data?.departments ?? []).map((d) => (
                    <SelectItem key={d} value={d}>{d}</SelectItem>
                  ))}
                  {form.department && !(optionsQuery.data?.departments ?? []).includes(form.department) && (
                    <SelectItem value={form.department}>{form.department}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Role</Label>
              <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select a role" /></SelectTrigger>
                <SelectContent>
                  {(optionsQuery.data?.roles ?? []).map((r) => (
                    <SelectItem key={r} value={r}>{r}</SelectItem>
                  ))}
                  {form.role && !(optionsQuery.data?.roles ?? []).includes(form.role) && (
                    <SelectItem value={form.role}>{form.role}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Joining Date</Label>
              <Input
                type="date" value={form.joining_date}
                onChange={(e) => setForm({ ...form, joining_date: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Salary (PKR)</Label>
              <Input
                type="number" value={form.salary_pkr}
                onChange={(e) => setForm({ ...form, salary_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Bonus</Label>
              <Input
                type="number" value={form.bonus_pkr}
                onChange={(e) => setForm({ ...form, bonus_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Deductions</Label>
              <Input
                type="number" value={form.deductions_pkr}
                onChange={(e) => setForm({ ...form, deductions_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
          <div className="flex items-center justify-between rounded-xl border px-3.5 py-3">
            <div>
              <Label className="text-sm">Active</Label>
              <p className="text-xs text-muted-foreground">Inactive employees are skipped by the next payroll run.</p>
            </div>
            <Switch checked={form.active} onCheckedChange={(v) => setForm({ ...form, active: v })} />
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.name.trim() || !form.department || !form.role || !form.salary_pkr || updateMutation.isPending}
            onClick={() =>
              updateMutation.mutate({
                name: form.name, department: form.department, role: form.role,
                salary_pkr: Number(form.salary_pkr),
                bonus_pkr: form.bonus_pkr ? Number(form.bonus_pkr) : 0,
                deductions_pkr: form.deductions_pkr ? Number(form.deductions_pkr) : 0,
                joining_date: form.joining_date, active: form.active,
              })
            }
          >
            {updateMutation.isPending ? "Saving…" : "Save Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteEmployeeButton({ employee }: { employee: Employee }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => deleteEmployee(employee.id),
    onSuccess: () => {
      toast.success(`${employee.name} removed`);
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["payroll-summary"] });
      setOpen(false);
    },
    onError: (error: Error) => {
      toast.error(error.message);
      setOpen(false);
    },
  });

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button
          variant="ghost" size="icon"
          className="h-8 w-8 rounded-lg text-muted-foreground hover:text-destructive"
        >
          <Trash2 className="h-4 w-4" />
          <span className="sr-only">Remove {employee.name}</span>
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {employee.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes their record. Employees who already have payroll history can&apos;t
            be removed this way — mark them inactive instead so their past payslips stay intact.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleteMutation.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "destructive" })}
            disabled={deleteMutation.isPending}
            onClick={(e) => {
              e.preventDefault();
              deleteMutation.mutate();
            }}
          >
            {deleteMutation.isPending ? "Removing…" : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ProcessPayrollButton() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: processPayroll,
    onSuccess: (result) => {
      if (result.processed_count === 0) {
        toast.info(`Everyone was already paid for ${result.period}`);
      } else {
        toast.success(`Paid ${result.processed_count} employee${result.processed_count === 1 ? "" : "s"} — ${money(result.total_net)}`);
        if (!result.expense_booked) {
          toast.warning("Employees were recorded as paid, but booking the expense in Transactions Service failed — check it later.");
        }
      }
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["payroll-summary"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Button variant="outline" className="gap-2 rounded-xl" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
      <Banknote className="h-4 w-4" /> {mutation.isPending ? "Processing…" : "Process Payroll"}
    </Button>
  );
}

function EmployeesPage() {
  const [search, setSearch] = useState("");

  const summaryQuery = useQuery({ queryKey: ["payroll-summary"], queryFn: payrollSummary });
  const employeesQuery = useQuery({
    queryKey: ["employees", search],
    queryFn: () => listEmployees({ ...(search.trim() ? { search: search.trim() } : {}), limit: 200 }),
  });

  const employees = employeesQuery.data?.employees ?? [];
  const byDept = Object.values(
    employees.reduce<Record<string, { name: string; value: number }>>((acc, e) => {
      acc[e.department] = { name: e.department, value: (acc[e.department]?.value ?? 0) + e.salary_pkr + e.bonus_pkr };
      return acc;
    }, {}),
  ).sort((a, b) => b.value - a.value);

  const s = summaryQuery.data;
  const summaryCards = s
    ? [
        { label: "Gross Payroll", value: s.total_salary + s.total_bonus, note: "Salaries + bonuses" },
        { label: "Deductions", value: s.total_deductions, note: "Tax, EOBI, advances" },
        { label: "Net Payable", value: s.total_net, note: `${s.period} · to be disbursed` },
        { label: "Bonuses", value: s.total_bonus, note: "Performance based" },
      ]
    : [];

  return (
    <>
      <PageHeader
        title="Employees & Payroll"
        subtitle={s ? `${s.employee_count} active employees · ${s.period} payroll cycle` : "Loading…"}
        actions={
          <>
            <SearchField placeholder="Search employees…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <ProcessPayrollButton />
            <AddEmployeeDialog />
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {summaryQuery.isLoading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)
          : summaryCards.map((s) => (
              <div key={s.label} className="surface lift p-5">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/15 text-accent-foreground">
                  <Wallet className="h-4.5 w-4.5" />
                </span>
                <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">{s.label}</p>
                <p className="mt-1 font-display text-2xl font-bold">{money(s.value)}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.note}</p>
              </div>
            ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <section className="surface overflow-hidden lg:col-span-2">
          <header className="border-b px-5 py-4">
            <h3 className="text-base font-semibold">Salary Sheet</h3>
            <p className="text-xs text-muted-foreground">
              {employeesQuery.data ? `${employeesQuery.data.total} employees` : "Loading…"}
            </p>
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
                  <TableHead>Employee</TableHead>
                  <TableHead className="w-36">Department</TableHead>
                  <TableHead className="w-32">Role</TableHead>
                  <TableHead className="w-28 text-right">Salary</TableHead>
                  <TableHead className="w-24 text-right">Bonus</TableHead>
                  <TableHead className="w-28 text-right">Deductions</TableHead>
                  <TableHead className="w-28 text-right">Net Salary</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead className="w-24 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employeesQuery.data?.employees.length === 0 && (
                  <TableRow><TableCell colSpan={9} className="py-10 text-center text-muted-foreground">No employees yet.</TableCell></TableRow>
                )}
                {employees.map((e: Employee) => (
                  <TableRow key={e.id} className="hover:bg-muted/50">
                    <TableCell>
                      <div className="flex min-w-0 items-center gap-3">
                        <Avatar className="h-9 w-9 shrink-0 border">
                          <AvatarFallback className="bg-primary/10 text-[11px] font-semibold text-primary">
                            {initials(e.name)}
                          </AvatarFallback>
                        </Avatar>
                        <span className="truncate font-medium">{e.name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{e.department}</TableCell>
                    <TableCell className="text-muted-foreground">{e.role ?? "—"}</TableCell>
                    <TableCell className="text-right">{money(e.salary_pkr)}</TableCell>
                    <TableCell className="text-right text-success">+{e.bonus_pkr.toLocaleString()}</TableCell>
                    <TableCell className="text-right text-destructive">-{e.deductions_pkr.toLocaleString()}</TableCell>
                    <TableCell className="text-right font-semibold">{money(e.net_salary_pkr)}</TableCell>
                    <TableCell>
                      <StatusBadge status={e.payment_status === "paid" ? "Paid" : "Pending"} />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <EditEmployeeDialog employee={e} />
                        <DeleteEmployeeButton employee={e} />
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
        <ChartCard title="Payroll by Department" subtitle="Gross cost">
          <HorizontalBars data={byDept} height={340} />
        </ChartCard>
      </div>
    </>
  );
}
