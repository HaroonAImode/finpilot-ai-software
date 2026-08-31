import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCircle2, Clock, Plus, Truck, X, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { PageHeader, StatusBadge } from "@/components/app-shell";
import {
  approveRequest, createOrder, createRequest, createVendorQuote, listOrders, listRequests,
  listVendorQuotes, procurementStats, rejectRequest, requestOptions, requestTimeline, scoreVendorQuote,
  updateOrderStatus,
  type PurchaseOrder, type PurchaseOrderStatus, type PurchaseRequest, type PurchaseRequestStatus,
} from "@/lib/procurement-service";

/** `?request=<id>` deep-links straight to one purchase request's timeline
 *  and vendor comparison — same pattern Revenue Manager's `?invoice=<id>`
 *  already established. */
const procurementSearchSchema = z.object({
  request: z.string().uuid().optional(),
});

export const Route = createFileRoute("/app/procurement")({
  head: () => ({
    meta: [
      { title: "Procurement — FinPilot AI" },
      { name: "description", content: "Purchase requests, orders, approvals and vendor comparison in one place." },
      { property: "og:title", content: "Procurement — FinPilot AI" },
      { property: "og:description", content: "Manage purchase requests, orders and vendor comparisons." },
    ],
  }),
  validateSearch: procurementSearchSchema,
  component: ProcurementPage,
});

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

const REQUEST_STATUS_LABELS: Record<PurchaseRequestStatus, string> = {
  pending_approval: "Pending Approval", approved: "Approved", rejected: "Rejected",
};

const ORDER_STATUS_LABELS: Record<PurchaseOrderStatus, string> = {
  in_transit: "In Transit", delivered: "Delivered", cancelled: "Cancelled",
};

function NewRequestDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ item_description: "", department: "", requester_name: "", amount_pkr: "" });

  const optionsQuery = useQuery({ queryKey: ["procurement-request-options"], queryFn: requestOptions, enabled: open });

  const createMutation = useMutation({
    mutationFn: createRequest,
    onSuccess: () => {
      toast.success("Purchase request submitted");
      queryClient.invalidateQueries({ queryKey: ["purchase-requests"] });
      queryClient.invalidateQueries({ queryKey: ["procurement-stats"] });
      setOpen(false);
      setForm({ item_description: "", department: "", requester_name: "", amount_pkr: "" });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> New Request
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Purchase Request</DialogTitle>
          <DialogDescription>Starts pending — approve it below before it can become an order.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>Item / Description</Label>
            <Input
              value={form.item_description} onChange={(e) => setForm({ ...form, item_description: e.target.value })}
              className="rounded-xl"
            />
          </div>
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
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Requester</Label>
              <Input
                value={form.requester_name} onChange={(e) => setForm({ ...form, requester_name: e.target.value })}
                className="rounded-xl"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Amount (PKR)</Label>
              <Input
                type="number" value={form.amount_pkr} onChange={(e) => setForm({ ...form, amount_pkr: e.target.value })}
                className="rounded-xl"
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.item_description.trim() || !form.department || !form.requester_name.trim() || !form.amount_pkr || createMutation.isPending}
            onClick={() =>
              createMutation.mutate({
                item_description: form.item_description, department: form.department,
                requester_name: form.requester_name, amount_pkr: Number(form.amount_pkr),
              })
            }
          >
            {createMutation.isPending ? "Submitting…" : "Submit Request"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NewOrderDialog({ approvedRequests }: { approvedRequests: PurchaseRequest[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    purchase_request_id: "", vendor_name: "", amount_pkr: "",
    expected_delivery: new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10),
  });

  const createMutation = useMutation({
    mutationFn: createOrder,
    onSuccess: () => {
      toast.success("Purchase order created");
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      queryClient.invalidateQueries({ queryKey: ["procurement-stats"] });
      setOpen(false);
      setForm({ purchase_request_id: "", vendor_name: "", amount_pkr: "", expected_delivery: form.expected_delivery });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> New Order
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Purchase Order</DialogTitle>
          <DialogDescription>Optionally created from an approved request.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>From Approved Request (optional)</Label>
            <Select
              value={form.purchase_request_id || "none"}
              onValueChange={(v) => setForm({ ...form, purchase_request_id: v === "none" ? "" : v })}
            >
              <SelectTrigger className="rounded-xl"><SelectValue placeholder="None" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None — standalone order</SelectItem>
                {approvedRequests.map((r) => (
                  <SelectItem key={r.id} value={r.id}>{r.item_description}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Vendor</Label>
            <Input value={form.vendor_name} onChange={(e) => setForm({ ...form, vendor_name: e.target.value })} className="rounded-xl" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Amount (PKR)</Label>
              <Input type="number" value={form.amount_pkr} onChange={(e) => setForm({ ...form, amount_pkr: e.target.value })} className="rounded-xl" />
            </div>
            <div className="space-y-1.5">
              <Label>Expected Delivery</Label>
              <Input type="date" value={form.expected_delivery} onChange={(e) => setForm({ ...form, expected_delivery: e.target.value })} className="rounded-xl" />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.vendor_name.trim() || !form.amount_pkr || createMutation.isPending}
            onClick={() =>
              createMutation.mutate({
                purchase_request_id: form.purchase_request_id || null, vendor_name: form.vendor_name,
                amount_pkr: Number(form.amount_pkr), expected_delivery: form.expected_delivery,
              })
            }
          >
            {createMutation.isPending ? "Creating…" : "Create Order"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddQuoteDialog({ requestId }: { requestId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ vendor_name: "", price_pkr: "", delivery_estimate: "", quality_rating: "", payment_terms: "" });

  const createMutation = useMutation({
    mutationFn: createVendorQuote,
    onSuccess: () => {
      toast.success("Quote added");
      queryClient.invalidateQueries({ queryKey: ["vendor-quotes", requestId] });
      setOpen(false);
      setForm({ vendor_name: "", price_pkr: "", delivery_estimate: "", quality_rating: "", payment_terms: "" });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2 rounded-xl">
          <Plus className="h-4 w-4" /> Add Quote
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Vendor Quote</DialogTitle>
          <DialogDescription>Recorded manually — score it once you've reviewed it.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label>Vendor</Label>
            <Input value={form.vendor_name} onChange={(e) => setForm({ ...form, vendor_name: e.target.value })} className="rounded-xl" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Price (PKR)</Label>
              <Input type="number" value={form.price_pkr} onChange={(e) => setForm({ ...form, price_pkr: e.target.value })} className="rounded-xl" />
            </div>
            <div className="space-y-1.5">
              <Label>Delivery Estimate</Label>
              <Input placeholder="e.g. 3 days" value={form.delivery_estimate} onChange={(e) => setForm({ ...form, delivery_estimate: e.target.value })} className="rounded-xl" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Quality Rating</Label>
              <Input placeholder="e.g. A+" value={form.quality_rating} onChange={(e) => setForm({ ...form, quality_rating: e.target.value })} className="rounded-xl" />
            </div>
            <div className="space-y-1.5">
              <Label>Payment Terms</Label>
              <Input placeholder="e.g. Net 30" value={form.payment_terms} onChange={(e) => setForm({ ...form, payment_terms: e.target.value })} className="rounded-xl" />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={!form.vendor_name.trim() || !form.price_pkr || createMutation.isPending}
            onClick={() =>
              createMutation.mutate({
                purchase_request_id: requestId, vendor_name: form.vendor_name, price_pkr: Number(form.price_pkr),
                delivery_estimate: form.delivery_estimate || null, quality_rating: form.quality_rating || null,
                payment_terms: form.payment_terms || null,
              })
            }
          >
            {createMutation.isPending ? "Adding…" : "Add Quote"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ScoreCell({ quoteId, score }: { quoteId: string; score: number | null }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(score?.toString() ?? "");
  const mutation = useMutation({
    mutationFn: (n: number) => scoreVendorQuote(quoteId, n),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vendor-quotes"] }),
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Input
      type="number" min={0} max={100} value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        const n = Number(value);
        if (value.trim() && !Number.isNaN(n) && n !== score) mutation.mutate(n);
      }}
      className="h-8 w-16 rounded-lg text-right"
      placeholder="—"
    />
  );
}

function RequestActions({ req }: { req: PurchaseRequest }) {
  const queryClient = useQueryClient();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["purchase-requests"] });
    queryClient.invalidateQueries({ queryKey: ["procurement-stats"] });
    queryClient.invalidateQueries({ queryKey: ["request-timeline"] });
  };
  const approveMutation = useMutation({
    mutationFn: () => approveRequest(req.id), onSuccess: () => { toast.success("Request approved"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });
  const rejectMutation = useMutation({
    mutationFn: () => rejectRequest(req.id), onSuccess: () => { toast.success("Request rejected"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  if (req.status !== "pending_approval") return null;
  return (
    <div className="flex items-center justify-end gap-1">
      <Button size="icon" variant="ghost" className="h-7 w-7 text-success hover:text-success" disabled={approveMutation.isPending || rejectMutation.isPending} onClick={() => approveMutation.mutate()} title="Approve">
        <Check className="h-4 w-4" />
      </Button>
      <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive hover:text-destructive" disabled={approveMutation.isPending || rejectMutation.isPending} onClick={() => rejectMutation.mutate()} title="Reject">
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

function ProcurementPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(search.request ?? null);

  const statsQuery = useQuery({ queryKey: ["procurement-stats"], queryFn: procurementStats });
  const requestsQuery = useQuery({ queryKey: ["purchase-requests"], queryFn: () => listRequests({ limit: 100 }) });
  const ordersQuery = useQuery({ queryKey: ["purchase-orders"], queryFn: () => listOrders({ limit: 100 }) });

  const requests = requestsQuery.data?.requests ?? [];
  const activeRequestId = selectedRequestId ?? requests[0]?.id ?? null;
  const activeRequest = requests.find((r) => r.id === activeRequestId) ?? null;
  const approvedRequests = requests.filter((r) => r.status === "approved");

  const timelineQuery = useQuery({
    queryKey: ["request-timeline", activeRequestId],
    queryFn: () => requestTimeline(activeRequestId as string),
    enabled: Boolean(activeRequestId),
  });
  const quotesQuery = useQuery({
    queryKey: ["vendor-quotes", activeRequestId],
    queryFn: () => listVendorQuotes(activeRequestId as string),
    enabled: Boolean(activeRequestId),
  });

  function selectRequest(id: string) {
    setSelectedRequestId(id);
    navigate({ search: { request: id } });
  }

  const s = statsQuery.data;
  const cards = s
    ? [
        { label: "Pending Procurement", value: s.pending_count, note: `${money(s.pending_amount_pkr)} committed`, icon: Clock, tone: "text-warning-foreground dark:text-warning bg-warning/15" },
        { label: "Completed", value: s.completed_count, note: "Orders delivered", icon: CheckCircle2, tone: "text-success bg-success/12" },
        { label: "Delayed", value: s.delayed_count, note: "Past expected delivery", icon: Truck, tone: "text-primary bg-primary/12" },
        { label: "Cancelled", value: s.cancelled_count, note: "Orders cancelled", icon: XCircle, tone: "text-destructive bg-destructive/12" },
      ]
    : [];

  return (
    <>
      <PageHeader
        title="Procurement"
        subtitle="From purchase request to delivery — fully tracked."
        actions={<NewRequestDialog />}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label} className="surface lift p-5">
            <span className={`grid h-10 w-10 place-items-center rounded-xl ${c.tone}`}>
              <c.icon className="h-4.5 w-4.5" />
            </span>
            <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">{c.label}</p>
            <p className="mt-1 font-display text-3xl font-bold">{c.value}</p>
            <p className="mt-1 text-xs text-muted-foreground">{c.note}</p>
          </div>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className="surface overflow-hidden xl:col-span-2">
          <header className="border-b px-5 py-4">
            <h3 className="text-base font-semibold">Purchase Requests</h3>
            <p className="text-xs text-muted-foreground">Click a row to see its timeline &amp; quotes</p>
          </header>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Item</TableHead>
                  <TableHead>Department</TableHead>
                  <TableHead>Requester</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">No purchase requests yet.</TableCell></TableRow>
                )}
                {requests.map((r) => (
                  <TableRow
                    key={r.id}
                    className={`cursor-pointer hover:bg-muted/50 ${r.id === activeRequestId ? "bg-muted/40" : ""}`}
                    onClick={() => selectRequest(r.id)}
                  >
                    <TableCell className="font-medium">{r.item_description}</TableCell>
                    <TableCell className="text-muted-foreground">{r.department}</TableCell>
                    <TableCell className="text-muted-foreground">{r.requester_name}</TableCell>
                    <TableCell className="text-right font-medium">{money(r.amount_pkr)}</TableCell>
                    <TableCell><StatusBadge status={REQUEST_STATUS_LABELS[r.status]} /></TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}><RequestActions req={r} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <section className="surface p-5">
          <h3 className="text-base font-semibold">Status Timeline</h3>
          <p className="text-xs text-muted-foreground">{activeRequest?.item_description ?? "Select a request"}</p>
          <ol className="mt-5 space-y-5">
            {(timelineQuery.data?.steps ?? []).map((t, i, arr) => (
              <li key={t.title} className="relative flex gap-3 pl-1">
                {i < arr.length - 1 && (
                  <span className="absolute left-[9px] top-5 h-full w-px bg-border" aria-hidden />
                )}
                <span
                  className={`z-10 mt-1 h-[18px] w-[18px] shrink-0 rounded-full border-2 ${
                    t.completed ? "border-primary bg-primary" : "border-border bg-card"
                  }`}
                />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{t.title}</p>
                  <p className="text-xs text-muted-foreground">{t.detail}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground/80">
                    {t.date ? new Date(t.date).toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : "—"}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="surface overflow-hidden">
          <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-5 py-4">
            <div>
              <h3 className="text-base font-semibold">Purchase Orders</h3>
              <p className="text-xs text-muted-foreground">Active &amp; recent</p>
            </div>
            <NewOrderDialog approvedRequests={approvedRequests} />
          </header>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Vendor</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Delivery</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(ordersQuery.data?.orders.length ?? 0) === 0 && (
                  <TableRow><TableCell colSpan={4} className="py-10 text-center text-muted-foreground">No purchase orders yet.</TableCell></TableRow>
                )}
                {ordersQuery.data?.orders.map((o) => (
                  <OrderRow key={o.id} order={o} />
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <section className="surface overflow-hidden">
          <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b px-5 py-4">
            <div className="min-w-0">
              <h3 className="text-base font-semibold">Vendor Comparison</h3>
              <p className="truncate text-xs text-muted-foreground">{activeRequest?.item_description ?? "Select a request"}</p>
            </div>
            {activeRequestId && <AddQuoteDialog requestId={activeRequestId} />}
          </header>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Vendor</TableHead>
                  <TableHead className="text-right">Quote</TableHead>
                  <TableHead>Delivery</TableHead>
                  <TableHead>Quality</TableHead>
                  <TableHead>Terms</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(quotesQuery.data?.quotes.length ?? 0) === 0 && (
                  <TableRow><TableCell colSpan={6} className="py-10 text-center text-muted-foreground">No quotes recorded yet.</TableCell></TableRow>
                )}
                {quotesQuery.data?.quotes.map((v) => (
                  <TableRow key={v.id} className="hover:bg-muted/50">
                    <TableCell className="font-medium">{v.vendor_name}</TableCell>
                    <TableCell className="text-right">{money(v.price_pkr)}</TableCell>
                    <TableCell className="text-muted-foreground">{v.delivery_estimate ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{v.quality_rating ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{v.payment_terms ?? "—"}</TableCell>
                    <TableCell className="text-right"><ScoreCell quoteId={v.id} score={v.score} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      </div>
    </>
  );
}

function OrderRow({ order }: { order: PurchaseOrder }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (status: PurchaseOrderStatus) => updateOrderStatus(order.id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
      queryClient.invalidateQueries({ queryKey: ["procurement-stats"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <TableRow className="hover:bg-muted/50">
      <TableCell>{order.vendor_name}</TableCell>
      <TableCell className="text-right font-medium">{money(order.amount_pkr)}</TableCell>
      <TableCell className="text-muted-foreground">{order.expected_delivery}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Select value={order.status} onValueChange={(v) => mutation.mutate(v as PurchaseOrderStatus)}>
            <SelectTrigger className="h-8 w-32 rounded-lg text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {(Object.keys(ORDER_STATUS_LABELS) as PurchaseOrderStatus[]).map((status) => (
                <SelectItem key={status} value={status}>{ORDER_STATUS_LABELS[status]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {order.delayed && <StatusBadge status="Delayed" />}
        </div>
      </TableCell>
    </TableRow>
  );
}
