import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plus, Search, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { PageHeader, StatusBadge } from "@/components/app-shell";
import { ChartCard, HorizontalBars } from "@/components/charts";
import { toast } from "sonner";
import {
  createVendor, listVendors, topVendors, vendorOptions,
  type Vendor, type VendorStatus,
} from "@/lib/vendors-service";

export const Route = createFileRoute("/app/vendors")({
  head: () => ({
    meta: [
      { title: "Vendors — FinPilot AI" },
      { name: "description", content: "Vendor directory with spend, ratings and payment terms for your suppliers." },
      { property: "og:title", content: "Vendors — FinPilot AI" },
      { property: "og:description", content: "Supplier directory with spend and performance ratings." },
    ],
  }),
  component: VendorsPage,
});

const STATUS_LABELS: Record<VendorStatus, string> = {
  active: "Active",
  inactive: "Inactive",
  review: "Review",
};

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "review", label: "Review" },
  { value: "inactive", label: "Inactive" },
] as const;

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

/** Spend is derived from Invoice Service on every read. When that call
 *  fails the backend flags it rather than reporting zero, and the UI has to
 *  respect that: showing a real supplier's spend as a confident "PKR 0"
 *  would be a false statement, not a missing one. */
function SpendCell({ vendor }: { vendor: Vendor }) {
  if (vendor.spend_unavailable) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Invoice Service could not be reached">
        <AlertTriangle className="h-3 w-3" /> Unavailable
      </span>
    );
  }
  return <span className="font-medium">{money(vendor.total_spend_pkr)}</span>;
}

function AddVendorDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "", category: "", city: "", ntn: "", payment_terms: "", rating: "",
    status: "active" as VendorStatus,
  });

  const optionsQuery = useQuery({
    queryKey: ["vendor-options"],
    queryFn: vendorOptions,
    // Suggestions only, and they never change at runtime — fetched once
    // when the dialog is first opened rather than on every page load.
    enabled: open,
  });

  const createMutation = useMutation({
    mutationFn: createVendor,
    onSuccess: (vendor) => {
      toast.success(`${vendor.name} added`);
      queryClient.invalidateQueries({ queryKey: ["vendors"] });
      setOpen(false);
      setForm({ name: "", category: "", city: "", ntn: "", payment_terms: "", rating: "", status: "active" });
    },
    // The backend returns a clear 409 for a duplicate name — surfaced as-is,
    // since "a vendor with this name already exists" is exactly what the
    // user needs to hear.
    onError: (error: Error) => toast.error(error.message || "Could not add this vendor"),
  });

  const submit = () => {
    if (!form.name.trim()) {
      toast.error("A vendor needs a name");
      return;
    }
    const rating = form.rating.trim() ? Number(form.rating) : null;
    if (rating !== null && (Number.isNaN(rating) || rating < 0 || rating > 5)) {
      toast.error("Rating must be between 0 and 5");
      return;
    }
    createMutation.mutate({
      name: form.name.trim(),
      category: form.category || null,
      city: form.city.trim() || null,
      ntn: form.ntn.trim() || null,
      payment_terms: form.payment_terms || null,
      rating,
      status: form.status,
    });
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> Add Vendor
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a vendor</DialogTitle>
          <DialogDescription>
            Spend is calculated from this vendor's invoices — it isn't entered here.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label className="text-xs">Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="ABC Traders"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Category</Label>
            <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
              <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select…" /></SelectTrigger>
              <SelectContent>
                {(optionsQuery.data?.categories ?? []).map((c) => (
                  <SelectItem key={c} value={c}>{c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">City</Label>
            <Input
              value={form.city}
              onChange={(e) => setForm({ ...form, city: e.target.value })}
              placeholder="Karachi"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Payment terms</Label>
            <Select value={form.payment_terms} onValueChange={(v) => setForm({ ...form, payment_terms: v })}>
              <SelectTrigger className="rounded-xl"><SelectValue placeholder="Select…" /></SelectTrigger>
              <SelectContent>
                {(optionsQuery.data?.payment_terms ?? []).map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">NTN</Label>
            <Input
              value={form.ntn}
              onChange={(e) => setForm({ ...form, ntn: e.target.value })}
              placeholder="3947261-8"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Rating (0–5)</Label>
            <Input
              value={form.rating}
              onChange={(e) => setForm({ ...form, rating: e.target.value })}
              placeholder="Leave blank if unrated"
              inputMode="decimal"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Status</Label>
            <Select
              value={form.status}
              onValueChange={(v) => setForm({ ...form, status: v as VendorStatus })}
            >
              <SelectTrigger className="rounded-xl"><SelectValue /></SelectTrigger>
              <SelectContent>
                {(["active", "review", "inactive"] as VendorStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABELS[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" className="rounded-xl" onClick={() => setOpen(false)}>Cancel</Button>
          <Button className="rounded-xl" onClick={submit} disabled={createMutation.isPending}>
            {createMutation.isPending ? "Adding…" : "Add vendor"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VendorsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<string>("all");

  const vendorsQuery = useQuery({
    queryKey: ["vendors", status, search],
    queryFn: () => listVendors({
      ...(status !== "all" ? { status: status as VendorStatus } : {}),
      ...(search.trim() ? { search: search.trim() } : {}),
      limit: 100,
    }),
  });

  const topQuery = useQuery({
    queryKey: ["vendors", "top"],
    queryFn: () => topVendors(5),
  });

  const vendors = vendorsQuery.data?.vendors ?? [];
  const total = vendorsQuery.data?.total ?? 0;

  // Only meaningful when spend actually came back — summing zeros from a
  // failed downstream call would present a fabricated total as fact.
  const spendKnown = vendors.length > 0 && !vendors[0]!.spend_unavailable;
  const totalSpend = spendKnown ? vendors.reduce((sum, v) => sum + v.total_spend_pkr, 0) : null;

  const chartData = (topQuery.data ?? [])
    .filter((v) => !v.spend_unavailable && v.total_spend_pkr > 0)
    .map((v) => ({ name: v.name, value: v.total_spend_pkr }));

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle={
          vendorsQuery.isPending
            ? "Loading…"
            : `${total} ${total === 1 ? "supplier" : "suppliers"}${
                totalSpend !== null ? ` · ${money(totalSpend)} total spend` : ""
              }`
        }
        actions={
          <>
            <div className="relative w-full sm:w-64">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search vendors…"
                className="rounded-xl pl-9"
              />
            </div>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-40 rounded-xl"><SelectValue /></SelectTrigger>
              <SelectContent>
                {STATUS_FILTERS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <AddVendorDialog />
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ChartCard title="Top Vendors by Spend" subtitle="Derived from linked invoices" className="lg:col-span-2">
          {topQuery.isPending ? (
            <Skeleton className="h-[320px] w-full" />
          ) : chartData.length === 0 ? (
            <div className="grid h-[320px] place-items-center text-center text-sm text-muted-foreground">
              <div>
                <p>No spend to chart yet</p>
                <p className="mt-1 text-xs">
                  Spend appears once invoices are linked to a vendor from the Scanner.
                </p>
              </div>
            </div>
          ) : (
            <HorizontalBars data={chartData} height={320} />
          )}
        </ChartCard>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-1">
          {vendorsQuery.isPending
            ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32 w-full rounded-xl" />)
            : vendors.slice(0, 3).map((v) => (
                <div key={v.id} className="surface lift p-5">
                  <div className="flex min-w-0 items-center gap-3">
                    <Avatar className="h-10 w-10 shrink-0 border">
                      <AvatarFallback className="bg-[image:var(--gradient-brand)] text-xs font-semibold text-primary-foreground">
                        {v.name.slice(0, 2).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{v.name}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {[v.category, v.city].filter(Boolean).join(" · ") || "No category yet"}
                      </p>
                    </div>
                  </div>
                  <div className="mt-4 flex items-center justify-between">
                    <span className="font-display text-lg font-bold">
                      <SpendCell vendor={v} />
                    </span>
                    {v.rating !== null && (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-warning">
                        <Star className="h-3.5 w-3.5 fill-current" /> {v.rating}
                      </span>
                    )}
                  </div>
                </div>
              ))}
        </div>
      </div>

      <section className="surface mt-6 overflow-hidden">
        <header className="border-b px-5 py-4">
          <h3 className="text-base font-semibold">Vendor Directory</h3>
          <p className="text-xs text-muted-foreground">All registered suppliers</p>
        </header>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {/* Explicit widths on the short-content columns — an HTML
                 * table's default layout otherwise stretches every column to
                 * fill the container, leaving large, empty-looking gaps
                 * (the same issue found and fixed on the Scanner page's own
                 * list). */}
                <TableHead>Vendor</TableHead>
                <TableHead className="w-36">Category</TableHead>
                <TableHead className="w-28">City</TableHead>
                <TableHead className="w-32 text-right">Total Spend</TableHead>
                <TableHead className="w-24 text-right">Invoices</TableHead>
                <TableHead className="w-24 text-right">Rating</TableHead>
                <TableHead className="w-28">Terms</TableHead>
                <TableHead className="w-28">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {vendorsQuery.isPending ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <TableRow key={i}><TableCell colSpan={8}><Skeleton className="h-6 w-full" /></TableCell></TableRow>
                ))
              ) : vendorsQuery.isError ? (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-sm text-destructive">
                    {(vendorsQuery.error as Error).message || "Could not load vendors"}
                  </TableCell>
                </TableRow>
              ) : vendors.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-sm text-muted-foreground">
                    {search || status !== "all"
                      ? "No vendors match this filter"
                      : "No vendors yet — add your first supplier to start tracking spend."}
                  </TableCell>
                </TableRow>
              ) : (
                vendors.map((v) => (
                  <TableRow key={v.id} className="hover:bg-muted/50">
                    <TableCell className="font-medium">{v.name}</TableCell>
                    <TableCell className="text-muted-foreground">{v.category ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{v.city ?? "—"}</TableCell>
                    <TableCell className="text-right"><SpendCell vendor={v} /></TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {v.spend_unavailable ? "—" : v.invoice_count}
                    </TableCell>
                    <TableCell className="text-right">{v.rating ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{v.payment_terms ?? "—"}</TableCell>
                    <TableCell><StatusBadge status={STATUS_LABELS[v.status]} /></TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </>
  );
}
