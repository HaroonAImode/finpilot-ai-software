import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Archive,
  CalendarRange,
  ChevronDown,
  ChevronRight,
  Download,
  FolderOpen,
  Loader2,
  MoreVertical,
  RotateCcw,
  Save,
  Wallet,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader, SearchField, StatusBadge } from "@/components/app-shell";
import { ChartCard, HorizontalBars } from "@/components/charts";
import { InvoiceThumbnail } from "@/components/records/invoice-thumbnail";
import { InvoiceDocumentPreview } from "@/components/records/invoice-preview";
import { NonTransactionalDocuments } from "@/components/records/non-transactional-documents";
import { NeedsReviewSection } from "@/components/records/needs-review-section";
import {
  fetchCategoryReport,
  getAvailableMonths,
  getCategorySummary,
  getInvoice,
  getMonthlySummary,
  invoiceCategoryOptions,
  listInvoices,
  rejectInvoice,
  updateInvoice,
  type CategorySummary,
  type Invoice,
  type InvoiceListItem,
  type InvoiceStatus,
  type InvoiceUpdatePayload,
  type MonthlyPaymentSummary,
  type MonthRange,
  type PaymentMethod,
} from "@/lib/invoice-service";

/** `?invoice=<id>` deep-links straight to one record, already open with its
 * preview — how the Scanner's own "Scanned Invoices" table opens a row back
 * into Saved Records, mirroring the exact same `?invoice=` convention the
 * Scanner route already uses for the reverse link. */
const recordsSearchSchema = z.object({
  invoice: z.string().uuid().optional(),
});

export const Route = createFileRoute("/app/records")({
  head: () => ({
    meta: [
      { title: "Saved Records — FinPilot AI" },
      {
        name: "description",
        content:
          "Every validated purchase as a categorized cashbook, with the receipt behind each entry.",
      },
    ],
  }),
  validateSearch: recordsSearchSchema,
  component: SavedRecords,
});

const UNCATEGORIZED = "Uncategorized";
const ALL_TIME = "__all_time__";

/** "2026-06" -> "June 2026" for the History picker's option labels. */
function formatMonthLabel(month: string): string {
  const [year, mon] = month.split("-").map(Number);
  return new Date(year!, (mon ?? 1) - 1, 1).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
}

/** "2026-06" -> inclusive {dateFrom, dateTo} spanning that whole month. */
function rangeForMonth(month: string | null): MonthRange | undefined {
  if (!month) return undefined;
  const [year, mon] = month.split("-").map(Number);
  const lastDay = new Date(year!, mon!, 0).getDate(); // day 0 of next month = last day of this one
  return { dateFrom: `${month}-01`, dateTo: `${month}-${String(lastDay).padStart(2, "0")}` };
}

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

/** What a human would call this row — same fallback order the category PDF
 *  report uses server-side (category_report_pdf.py's _particulars), so the
 *  screen and the downloaded report never describe the same entry two
 *  different ways. Never invents a description: when nothing usable was
 *  extracted, this says so plainly rather than falling back to a generic
 *  "Untitled". */
function particulars(
  row: Pick<InvoiceListItem, "vendor_name" | "invoice_number" | "filename">,
): string {
  return row.vendor_name || row.invoice_number || row.filename || "Unknown / Needs Review";
}

/** The Vendor/Payee column specifically — narrower than `particulars`,
 *  which also falls back to an invoice number or filename when no vendor
 *  was extracted. Kept separate so a row whose particular ended up being
 *  an invoice number still honestly shows "Not Specified" here rather
 *  than repeating that invoice number as if it were the vendor. */
function vendorLabel(row: Pick<InvoiceListItem, "vendor_name">): string {
  return row.vendor_name || "Not Specified";
}

/** The Expense Date column. Was previously `row.invoice_date ?? new
 *  Date(row.created_at).toLocaleDateString()` — silently showing the date
 *  this record was *scanned* whenever no real expense date was extracted,
 *  indistinguishable on screen from a genuine one (an audit against the
 *  real petty-cash Excel caught this: several records with no extracted
 *  date were showing today's scan date as if it were the expense date).
 *  That is exactly the silent-guess the rest of this page goes out of its
 *  way to avoid — a missing date says so plainly instead. */
function dateLabel(row: Pick<InvoiceListItem, "invoice_date">): string {
  return row.invoice_date ?? "Unknown date";
}

/** "minute_sheet" -> "Minute Sheet" — the rules engine's own verdict
 *  (Invoice.detected_document_type), never guessed or invented here. */
function documentTypeLabel(row: Pick<InvoiceListItem, "detected_document_type">): string {
  if (!row.detected_document_type) return "Unknown";
  return row.detected_document_type
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

const STATUS_OPTIONS = [
  { value: "validated", label: "Validated" },
  { value: "sent_to_accounting", label: "Sent to Accounting" },
] as const;

const PAYMENT_METHOD_OPTIONS = [
  { value: "__none__", label: "Not Specified" },
  { value: "cash", label: "Cash" },
  { value: "bank", label: "Online" },
] as const;

/** Explicit return type is load-bearing, not decoration: without it,
 *  `payment_method`'s fresh "__none__" literal widens to plain `string` on
 *  return (TypeScript's usual literal-widening for an unannotated function's
 *  return object), collapsing `PaymentMethod | "__none__"` down to `string`
 *  and losing the type safety the sentinel value was supposed to have. */
interface RecordEditForm {
  vendor_name: string;
  category: string;
  invoice_date: string;
  total: string;
  payment_method: PaymentMethod | "__none__";
  status: InvoiceStatus;
}

function editFormFromInvoice(invoice: Invoice): RecordEditForm {
  return {
    vendor_name: invoice.vendor_name ?? "",
    category: invoice.category ?? "",
    invoice_date: invoice.invoice_date ?? "",
    total: invoice.total?.toString() ?? "",
    payment_method: invoice.payment_method ?? "__none__",
    status: invoice.status,
  };
}

function numOrNull(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

/** Cash/Online/Not Specified, editable right on the row — not just after
 *  opening the full record detail. Same three options, same never-guess
 *  rule as everywhere else this appears: a change here still goes through
 *  the one real endpoint (`PUT /invoices/{id}`), it's just reachable
 *  without a click into the detail panel first. `stopPropagation` on the
 *  trigger keeps a click here from also opening that panel — same
 *  reasoning, and the same technique, as the row's own 3-dot menu button
 *  right next to it. */
function InlinePaymentMethodSelect({
  value,
  onChange,
}: {
  value: PaymentMethod | null;
  onChange: (method: PaymentMethod | null) => void;
}) {
  return (
    <Select
      value={value ?? "__none__"}
      onValueChange={(v) => onChange(v === "__none__" ? null : (v as PaymentMethod))}
    >
      <SelectTrigger
        onClick={(e) => e.stopPropagation()}
        title={value ? "Change payment method" : "Set payment method — Cash or Online"}
        className={`h-6 w-fit shrink-0 gap-1 rounded-full border px-2.5 text-[11px] font-medium shadow-none focus:ring-1 focus:ring-ring focus:ring-offset-0 [&>svg]:h-3 [&>svg]:w-3 [&>svg]:opacity-70 ${
          value
            ? "border-accent/40 bg-accent/10 text-accent-foreground dark:text-accent"
            : "border-dashed border-muted-foreground/50 bg-transparent text-muted-foreground hover:border-muted-foreground hover:text-foreground"
        }`}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="start" onClick={(e) => e.stopPropagation()}>
        {PAYMENT_METHOD_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** One category's compact, expandable section in the left pane. The total
 *  shown here always comes from `summary` — the backend's own SQL
 *  aggregate (GET /invoices/categories/summary) — never a sum of `rows`,
 *  which may be fewer than the category's real count once this page's own
 *  fetch limit is in play. See the design spec §7. */
function CategorySection({
  category,
  rows,
  summary,
  selectedId,
  hasPinned,
  onSelect,
  onHoverChange,
  downloading,
  onDownload,
  categoryOptions,
  onChangeCategory,
  onRemove,
  onChangePaymentMethod,
}: {
  category: string | null;
  rows: InvoiceListItem[];
  summary: CategorySummary | undefined;
  selectedId: string | null;
  /** Something is already pinned in the right pane. Hovering a *different*
   *  row must not swap that whole pane out from under the user (real layout
   *  thrash, reported directly) — so in this state, the row's own hover
   *  drives a small tooltip on the thumbnail instead of the large panel. */
  hasPinned: boolean;
  onSelect: (id: string) => void;
  onHoverChange: (id: string | null) => void;
  downloading: boolean;
  onDownload: () => void;
  categoryOptions: string[];
  onChangeCategory: (invoiceId: string, category: string | null) => void;
  onRemove: (invoiceId: string) => void;
  onChangePaymentMethod: (invoiceId: string, method: PaymentMethod | null) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const label = category ?? UNCATEGORIZED;

  return (
    <div className="border-b last:border-b-0">
      <div className="flex items-center gap-2 border-b border-l-2 border-l-accent/70 bg-muted/60 px-4 py-3.5">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate font-display text-base font-bold tracking-wide">{label}</span>
          <span className="shrink-0 rounded-full bg-background px-2 py-0.5 text-xs font-semibold text-muted-foreground shadow-sm">
            {summary ? summary.count : rows.length}
          </span>
        </button>
        <span className="shrink-0 text-base font-bold tabular-nums text-accent-foreground dark:text-accent">
          {summary ? (
            money(summary.total)
          ) : (
            <Skeleton className="inline-block h-5 w-20 align-middle" />
          )}
        </span>
        <Button
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0 rounded-md text-muted-foreground hover:text-foreground"
          title={`Download ${label} as PDF`}
          disabled={downloading}
          onClick={onDownload}
        >
          {downloading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {!collapsed && (
        <div>
          {rows.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              No entries in this category yet.
            </p>
          )}
          {rows.map((row) => (
            // A <div role="button">, not a <button> — the 3-dot category
            // menu below is a real <button> of its own, and nesting a
            // button inside a button is invalid HTML.
            <div
              key={row.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(row.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelect(row.id);
              }}
              // Only drives the large right-pane preview while nothing is
              // pinned yet — once something is, this row's own thumbnail
              // tooltip carries hover instead (passed via smallHoverPreview
              // below), and the panel itself stays untouched by hover.
              {...(hasPinned
                ? {}
                : {
                    onMouseEnter: () => onHoverChange(row.id),
                    onMouseLeave: () => onHoverChange(null),
                    onFocus: () => onHoverChange(row.id),
                    onBlur: () => onHoverChange(null),
                  })}
              className={`flex w-full cursor-pointer items-center gap-3 border-t px-4 py-2 text-left transition-colors first:border-t-0 hover:bg-muted/50 ${
                selectedId === row.id ? "bg-accent/10" : ""
              }`}
            >
              <InvoiceThumbnail
                invoiceId={row.id}
                label={particulars(row)}
                smallHoverPreview={hasPinned}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{particulars(row)}</span>
                <span className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                  <span>{dateLabel(row)}</span>
                  <span aria-hidden>·</span>
                  <span className="truncate">{vendorLabel(row)}</span>
                  <span aria-hidden>·</span>
                  <span>{documentTypeLabel(row)}</span>
                </span>
              </span>
              {/* Its own slot, right before the amount — not buried as one
               *  more "·"-separated word in the muted metadata line, where
               *  it read as description text nobody would think to click.
               *  Styled as a pill (dashed border while unset) specifically
               *  so it reads as a control, the same convention an empty
               *  "+ Add tag" chip uses elsewhere on the web. */}
              <InlinePaymentMethodSelect
                value={row.payment_method}
                onChange={(method) => onChangePaymentMethod(row.id, method)}
              />
              <span className="shrink-0 text-sm font-semibold tabular-nums">
                {row.total !== null ? money(row.total) : "—"}
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    onClick={(e) => e.stopPropagation()}
                    title="Record actions"
                    className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <MoreVertical className="h-4 w-4" />
                  </button>
                </DropdownMenuTrigger>
                {/* Two levels on purpose: this used to dump the full category
                 *  list straight into the menu, so every click here first
                 *  had to scan past ~10 categories to find any other action.
                 *  Actions first, categories only after choosing "Change
                 *  Category" — same pattern a file manager's right-click
                 *  menu uses for a "Move to folder ▸" submenu. */}
                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>Change Category</DropdownMenuSubTrigger>
                    <DropdownMenuSubContent className="max-h-72 overflow-y-auto">
                      <DropdownMenuItem onSelect={() => onChangeCategory(row.id, null)}>
                        {UNCATEGORIZED}
                      </DropdownMenuItem>
                      {categoryOptions.map((c) => (
                        <DropdownMenuItem key={c} onSelect={() => onChangeCategory(row.id, c)}>
                          {c}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                  <DropdownMenuSeparator />
                  {/* Not a hard delete — this app has none by design (see
                   *  POST /reject's own docstring): the file, extraction and
                   *  audit trail all stay intact and this is reversible. It
                   *  just leaves the cashbook's validated/sent-to-accounting
                   *  view, which is what "delete it" means from here. */}
                  <DropdownMenuItem
                    className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                    onSelect={() => onRemove(row.id)}
                  >
                    Remove from Cashbook
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The right pane's live preview while hovering a row — large and clear,
 *  not the small floating popup a tooltip would give. Deliberately lighter
 *  than RecordDetail: no edit form, because a value that changes under the
 *  cursor as it merely passes over different rows would be confusing to
 *  edit. Click the row to pin it and actually make changes. */
function QuickLookPreview({ row }: { row: InvoiceListItem }) {
  return (
    <div className="flex flex-col gap-4">
      <InvoiceDocumentPreview invoiceId={row.id} filename={row.filename} />
      <div className="flex items-start justify-between gap-3 border-t pt-4">
        <div className="min-w-0">
          <p className="truncate font-display text-base font-bold">{particulars(row)}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {row.category ?? UNCATEGORIZED} · {dateLabel(row)}
          </p>
        </div>
        <p className="shrink-0 text-lg font-bold tabular-nums">
          {row.total !== null ? money(row.total) : "—"}
        </p>
      </div>
      <p className="text-xs text-muted-foreground">Quick look — click the entry to edit it.</p>
    </div>
  );
}

/** The same close button Windows' own title bars use: a neutral glyph that
 *  blends into the surface until you actually aim for it, then turns solid
 *  red with a white X — the one control on the page that should look
 *  slightly alarming to hover, since it is about to make something go away. */
function WindowsCloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      title="Close"
      aria-label="Close preview"
      className="absolute right-0 top-0 z-10 grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
    >
      <X className="h-4 w-4" />
    </button>
  );
}

function RecordDetail({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const invoiceQuery = useQuery({
    queryKey: ["invoice", invoiceId],
    queryFn: () => getInvoice(invoiceId),
  });
  const optionsQuery = useQuery({ queryKey: ["invoice-options"], queryFn: invoiceCategoryOptions });

  const [form, setForm] = useState<ReturnType<typeof editFormFromInvoice> | null>(null);
  const active = form ?? (invoiceQuery.data ? editFormFromInvoice(invoiceQuery.data) : null);

  const updateMutation = useMutation({
    mutationFn: (payload: InvoiceUpdatePayload) => updateInvoice(invoiceId, payload),
    onSuccess: () => {
      toast.success("Record saved");
      queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-category-summary"] });
      // A payment-method (or total/date) edit here must be reflected in the
      // Cash Book strip immediately, not just on the next full reload.
      queryClient.invalidateQueries({ queryKey: ["invoice-monthly-summary"] });
      queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      setForm(null);
    },
    onError: (error: Error) => toast.error(error.message || "Could not save this record"),
  });

  if (invoiceQuery.isLoading || !active) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-72 w-full rounded-xl" />
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (invoiceQuery.isError || !invoiceQuery.data) {
    return <p className="text-sm text-muted-foreground">Could not load this record.</p>;
  }

  const invoice = invoiceQuery.data;
  const original = editFormFromInvoice(invoice);
  const dirty = JSON.stringify(active) !== JSON.stringify(original);
  const totalInvalid = active.total.trim() !== "" && Number.isNaN(Number(active.total));

  const set = <K extends keyof typeof active>(key: K, value: (typeof active)[K]) =>
    setForm({ ...active, [key]: value });

  return (
    <div className="relative flex flex-col gap-5">
      <WindowsCloseButton onClose={onClose} />
      <InvoiceDocumentPreview
        invoiceId={invoice.id}
        filename={invoice.filename}
        pageCount={invoice.page_dimensions.length || 1}
      />

      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2 space-y-1.5">
          <Label>Vendor / Particulars</Label>
          <Input
            value={active.vendor_name}
            onChange={(e) => set("vendor_name", e.target.value)}
            className="rounded-xl"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Category</Label>
          <Select
            value={active.category || UNCATEGORIZED}
            onValueChange={(v) => set("category", v === UNCATEGORIZED ? "" : v)}
          >
            <SelectTrigger className="rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNCATEGORIZED}>{UNCATEGORIZED}</SelectItem>
              {(optionsQuery.data?.categories ?? []).map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Date</Label>
          <Input
            type="date"
            value={active.invoice_date}
            onChange={(e) => set("invoice_date", e.target.value)}
            className="rounded-xl"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Amount (PKR)</Label>
          <Input
            type="text"
            inputMode="decimal"
            value={active.total}
            onChange={(e) => set("total", e.target.value)}
            className={`rounded-xl text-right tabular-nums ${totalInvalid ? "border-destructive text-destructive" : ""}`}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Payment</Label>
          <Select
            value={active.payment_method ?? "__none__"}
            onValueChange={(v) => set("payment_method", v as PaymentMethod | "__none__")}
          >
            <SelectTrigger className="rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAYMENT_METHOD_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="col-span-2 space-y-1.5">
          <Label>Status</Label>
          <Select
            value={active.status}
            onValueChange={(v) => set("status", v as typeof active.status)}
          >
            <SelectTrigger className="rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex items-center justify-end border-t pt-4">
        <div className="flex gap-2">
          {dirty && (
            <Button
              variant="outline"
              className="gap-1.5 rounded-xl"
              onClick={() => setForm(null)}
              disabled={updateMutation.isPending}
            >
              <RotateCcw className="h-3.5 w-3.5" /> Revert
            </Button>
          )}
          <Button
            className="gap-1.5 rounded-xl"
            disabled={!dirty || totalInvalid || updateMutation.isPending}
            onClick={() => {
              if (totalInvalid) {
                toast.error("Fix the amount before saving");
                return;
              }
              const paymentMethod = active.payment_method;
              updateMutation.mutate({
                vendor_name: active.vendor_name.trim() || null,
                category: active.category.trim() || null,
                invoice_date: active.invoice_date.trim() || null,
                total: numOrNull(active.total),
                payment_method: paymentMethod === "__none__" ? null : paymentMethod,
                status: active.status as "validated" | "sent_to_accounting",
              });
            }}
          >
            <Save className="h-3.5 w-3.5" /> {updateMutation.isPending ? "Saving…" : "Save Changes"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** One stat in the Cash Book summary strip below — same typography as the
 *  Dashboard's own KpiCard (uppercase tracking-wide label, bold display
 *  value) so this reads as the same kind of number everywhere else in the
 *  app already shows one, just laid out compactly for this denser page. */
function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="truncate text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-display text-xl font-bold tabular-nums tracking-tight">{value}</p>
    </div>
  );
}

/** The Cash Book's Total/Cash/Online/Not-Specified/Transactions strip —
 *  every number here comes straight from GET /invoices/monthly-summary
 *  (a backend SQL aggregate over transactional records only, §12), never
 *  computed client-side from whatever rows happen to be loaded. Reconciles
 *  by construction: cash + online + unspecified always equals the total.
 *
 *  `month` is purely a label — which period these numbers cover — so it's
 *  shown regardless of loading state; it's the one piece of context that
 *  doesn't come from the summary query and shouldn't disappear/skeleton
 *  along with it. Rendered in the design system's own accent color (the
 *  same teal token used elsewhere, defined per-theme in styles.css) so it
 *  reads clearly against both light and dark surfaces without a one-off color. */
function MonthlySummaryStrip({
  summary,
  isLoading,
  month,
}: {
  summary: MonthlyPaymentSummary | undefined;
  isLoading: boolean;
  month: string | null;
}) {
  return (
    <div className="surface mt-4 p-4">
      <p className="mb-3 font-display text-lg font-extrabold uppercase tracking-wide text-accent">
        {month ? formatMonthLabel(month) : "All Time"}
      </p>
      {isLoading || !summary ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-11 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <SummaryStat label="Total Expenses" value={money(summary.monthly_total)} />
          <SummaryStat label="Cash" value={money(summary.cash_total)} />
          <SummaryStat label="Online" value={money(summary.online_total)} />
          <SummaryStat label="Not Specified" value={money(summary.unspecified_total)} />
          <SummaryStat label="Transactions" value={summary.transaction_count.toLocaleString()} />
        </div>
      )}
    </div>
  );
}

function Overview({ summaries }: { summaries: CategorySummary[] }) {
  const grandTotal = summaries.reduce((sum, s) => sum + s.total, 0);
  const chartData = summaries
    .filter((s) => s.total > 0)
    .map((s) => ({ name: s.category ?? UNCATEGORIZED, value: s.total }));

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 py-10 text-center">
      <span className="grid h-14 w-14 place-items-center rounded-2xl bg-accent/15 text-accent-foreground dark:text-accent">
        <FolderOpen className="h-6 w-6" />
      </span>
      <div>
        <p className="font-display text-3xl font-bold">{money(grandTotal)}</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Across {summaries.length} categor{summaries.length === 1 ? "y" : "ies"} · click an entry
          to see its receipt
        </p>
      </div>
      {chartData.length > 0 && (
        <div className="w-full max-w-lg text-left">
          <ChartCard title="Spend by Category" subtitle="Validated & sent-to-accounting purchases">
            <HorizontalBars data={chartData} height={Math.max(180, chartData.length * 34)} />
          </ChartCard>
        </div>
      )}
    </div>
  );
}

function SavedRecords() {
  const queryClient = useQueryClient();
  const routeSearch = Route.useSearch();
  const [search, setSearch] = useState("");
  // Seeded from ?invoice=<id> (the Scanner's own "open in Saved Records"
  // action lands here) so the deep link opens straight into that record's
  // detail/preview instead of the plain category overview.
  const [selectedId, setSelectedId] = useState<string | null>(routeSearch.invoice ?? null);
  // Live "quick look" — updates as the cursor moves over a different row,
  // reverts the instant it leaves. Separate from selectedId, which stays
  // pinned in the right pane until a row is actually clicked (or a click
  // elsewhere clears it) — hover previews, click pins, same distinction
  // InvoiceThumbnail's own lightbox already draws for a single thumbnail.
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [downloadingCategory, setDownloadingCategory] = useState<string | null>(null);
  // null = All Time. Set from the History picker — every query below scopes
  // to this same month, so the list, the totals, and a downloaded report
  // can never disagree about which period they're each showing.
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const monthRange = rangeForMonth(selectedMonth);

  const monthsQuery = useQuery({
    queryKey: ["invoice-available-months"],
    queryFn: getAvailableMonths,
  });
  const recordsQuery = useQuery({
    queryKey: ["invoices", "records", selectedMonth],
    // transactionStatus is what keeps a *confirmed* internal memo
    // (non_transactional + validated) out of the cashbook — without it,
    // confirming one would land it here as an "Uncategorized" row, the
    // exact outcome the non-transactional area exists to avoid.
    queryFn: () =>
      listInvoices({
        status: ["validated", "sent_to_accounting"],
        transactionStatus: "transactional",
        limit: 500,
        ...monthRange,
      }),
  });
  const summaryQuery = useQuery({
    queryKey: ["invoice-category-summary", selectedMonth],
    queryFn: () => getCategorySummary(monthRange),
  });
  const monthlySummaryQuery = useQuery({
    queryKey: ["invoice-monthly-summary", selectedMonth],
    queryFn: () => getMonthlySummary(monthRange),
  });
  const optionsQuery = useQuery({ queryKey: ["invoice-options"], queryFn: invoiceCategoryOptions });

  const changeCategoryMutation = useMutation({
    mutationFn: ({ id, category }: { id: string; category: string | null }) =>
      updateInvoice(id, { category }),
    onSuccess: (updated) => {
      toast.success(`Moved to ${updated.category ?? UNCATEGORIZED}`);
      queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-category-summary"] });
      queryClient.invalidateQueries({ queryKey: ["invoice", updated.id] });
    },
    onError: (error: Error) =>
      toast.error(error.message || "Could not change this record's category"),
  });

  // "Remove from Cashbook" — not a hard delete (this app has none, see
  // POST /reject's own docstring: reversible, keeps the file/extraction/
  // audit trail intact). Rejecting moves status off validated/sent-to-
  // accounting, which is exactly what drops it out of every query on this
  // page — closes the right pane too, in case the removed row was open.
  const removeMutation = useMutation({
    mutationFn: (id: string) => rejectInvoice(id),
    onSuccess: (_updated, id) => {
      toast.success("Removed from Cashbook");
      queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-category-summary"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-monthly-summary"] });
      setSelectedId((current) => (current === id ? null : current));
    },
    onError: (error: Error) => toast.error(error.message || "Could not remove this record"),
  });

  // The inline per-row Payment Method dropdown — same endpoint and same
  // three-way Cash/Online/Not Specified choice as RecordDetail's own
  // Payment field, just reachable without opening it first.
  const paymentMethodMutation = useMutation({
    mutationFn: ({ id, payment_method }: { id: string; payment_method: PaymentMethod | null }) =>
      updateInvoice(id, { payment_method }),
    onSuccess: () => {
      toast.success("Payment method updated");
      queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-monthly-summary"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not update payment method"),
  });

  const rows = useMemo(() => {
    const all = recordsQuery.data?.invoices ?? [];
    const term = search.trim().toLowerCase();
    if (!term) return all;
    return all.filter((r) => particulars(r).toLowerCase().includes(term));
  }, [recordsQuery.data, search]);

  const grouped = useMemo(() => {
    const map = new Map<string | null, InvoiceListItem[]>();
    for (const row of rows) {
      const key = row.category ?? null;
      const bucket = map.get(key);
      if (bucket) bucket.push(row);
      else map.set(key, [row]);
    }
    return map;
  }, [rows]);

  // Section order/totals come from the summary endpoint (already sorted,
  // Uncategorized last) — never from whatever order `grouped` happens to
  // iterate in, and a category with zero rows on this page (but a nonzero
  // summary count, if the 500-row fetch above were ever exceeded) still
  // gets a section rather than silently vanishing.
  const sections: { category: string | null; rows: InvoiceListItem[] }[] = summaryQuery.data
    ? summaryQuery.data.map((s) => ({ category: s.category, rows: grouped.get(s.category) ?? [] }))
    : Array.from(grouped.entries()).map(([category, rows]) => ({ category, rows }));

  // Already in memory from the list fetch — no extra request just to show
  // a quick look while hovering.
  const hoveredRow = hoveredId ? (rows.find((r) => r.id === hoveredId) ?? null) : null;

  async function downloadCategory(category: string | null) {
    const key = category ?? UNCATEGORIZED;
    setDownloadingCategory(key);
    try {
      const blob = await fetchCategoryReport(key, monthRange);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${key.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-report.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not generate this report");
    } finally {
      setDownloadingCategory(null);
    }
  }

  return (
    <TooltipProvider>
      <PageHeader
        title="Saved Records"
        subtitle={
          selectedMonth
            ? `${formatMonthLabel(selectedMonth)} — a categorized cashbook, with the receipt behind each entry.`
            : "Every validated purchase, grouped like a cashbook — with the receipt behind each entry."
        }
        actions={
          <>
            <Select
              value={selectedMonth ?? ALL_TIME}
              onValueChange={(v) => setSelectedMonth(v === ALL_TIME ? null : v)}
            >
              <SelectTrigger className="w-44 gap-1.5 rounded-xl" title="History — browse by month">
                <CalendarRange className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_TIME}>All Time</SelectItem>
                {(monthsQuery.data ?? []).map((m) => (
                  <SelectItem key={m} value={m}>
                    {formatMonthLabel(m)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <SearchField
              placeholder="Search vendor, invoice #…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </>
        }
      />

      <MonthlySummaryStrip
        summary={monthlySummaryQuery.data}
        isLoading={monthlySummaryQuery.isPending}
        month={selectedMonth}
      />

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[420px_1fr]">
        <section className="surface flex flex-col overflow-hidden">
          <header className="flex items-center justify-between border-b px-4 py-3">
            <div>
              <h3 className="text-sm font-semibold">Categories</h3>
              <p className="text-xs text-muted-foreground">
                {recordsQuery.data
                  ? `${recordsQuery.data.total} total`
                  : recordsQuery.isError
                    ? "Could not load"
                    : "Loading…"}
              </p>
            </div>
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent-foreground dark:text-accent">
              <Wallet className="h-4 w-4" />
            </span>
          </header>

          {/* Grows with its content and scrolls with the page — same as its
           *  sibling panel on the right (Overview/RecordDetail), which has
           *  never had a height cap. This used to be capped at
           *  `max-h-[calc(100vh-220px)]` with its own inner scrollbar, which
           *  was fragile: real reports at normal browser zoom (not just
           *  extreme values) showed it collapsing down to a couple of
           *  visible rows in a tiny nested scrollbox, because a fixed pixel
           *  budget for "everything above this panel" can't actually track
           *  the real height of that content (which grew again the moment
           *  the Cash Book summary strip above gained its own month-name
           *  line). Letting the page scroll instead removes the whole class
           *  of bug rather than re-tuning a number that will just drift out
           *  of date again. */}
          <div>
            {recordsQuery.isPending && (
              <div className="space-y-2 p-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-lg" />
                ))}
              </div>
            )}
            {recordsQuery.isError && (
              <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-sm">
                <AlertCircle className="h-5 w-5 text-destructive" />
                <p className="font-medium">Could not load Saved Records</p>
                <p className="text-xs text-muted-foreground">
                  {recordsQuery.error instanceof Error
                    ? recordsQuery.error.message
                    : "Something went wrong."}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-1 rounded-xl"
                  onClick={() => recordsQuery.refetch()}
                >
                  Retry
                </Button>
              </div>
            )}
            {!recordsQuery.isPending && !recordsQuery.isError && rows.length === 0 && (
              <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
                <Archive className="h-5 w-5" />
                {search
                  ? "No records match your search."
                  : selectedMonth
                    ? `No expenses recorded for ${formatMonthLabel(selectedMonth)}.`
                    : "No saved records yet — validate an invoice from the Scanner."}
              </div>
            )}
            {sections.map(({ category, rows: sectionRows }) => (
              <CategorySection
                key={category ?? "__uncategorized__"}
                category={category}
                rows={sectionRows}
                summary={summaryQuery.data?.find((s) => s.category === category)}
                selectedId={selectedId}
                hasPinned={Boolean(selectedId)}
                onSelect={(id) => {
                  setSelectedId(id);
                  setHoveredId(null);
                }}
                onHoverChange={setHoveredId}
                downloading={downloadingCategory === (category ?? UNCATEGORIZED)}
                onDownload={() => downloadCategory(category)}
                categoryOptions={optionsQuery.data?.categories ?? []}
                onChangeCategory={(id, newCategory) =>
                  changeCategoryMutation.mutate({ id, category: newCategory })
                }
                onRemove={(id) => removeMutation.mutate(id)}
                onChangePaymentMethod={(id, payment_method) =>
                  paymentMethodMutation.mutate({ id, payment_method })
                }
              />
            ))}
          </div>

          {/* Documents awaiting human review before posting to official Cash Book */}
          <div className="mt-8">
            <NeedsReviewSection />
          </div>

          {/* Documents the system identified as *not* financial
              transactions — kept visible and explained here rather than
              dropped into the cashbook as "Uncategorized". */}
          <div className="mt-6 border-t pt-6">
            <NonTransactionalDocuments />
          </div>
        </section>

        {/* `lg:sticky` (only where the 2-column layout actually applies —
         *  it stacks to one column below that) + `self-start` so it pins in
         *  place while the categories list to its left scrolls past, rather
         *  than scrolling away with the page. `lg:top-16` clears the app
         *  shell's own sticky header (`sticky top-0`, app-shell.tsx) so this
         *  panel's top edge never sits underneath it. This panel never had
         *  a height cap of its own (unlike the categories list before this
         *  audit) — it only *looked* pinned before because the whole page
         *  never needed to scroll; now that a long categories list can make
         *  it scroll, this needs to say so explicitly. */}
        <section className="surface self-start overflow-y-auto p-6 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)]">
          {/* The *actual* root cause of "clicking another entry keeps
           *  showing the previous one": once something is pinned, hover
           *  handlers are removed from every row (see CategorySection's own
           *  `hasPinned ? {} : {...}` above) — so `hoveredId` freezes at
           *  whatever was last hovered *before* the first click and never
           *  updates again. The old condition here (`hoveredId !==
           *  selectedId`) then stayed true forever for every subsequent
           *  click that landed on a different row than that frozen hover,
           *  so it kept rendering the stale QuickLookPreview instead of the
           *  just-selected RecordDetail. `key={selectedId}` (below) fixed a
           *  *different* leak — this is the one that actually matched the
           *  report. A pinned selection must always win, full stop: hover
           *  only gets a say while nothing is pinned yet. */}
          {!selectedId && hoveredRow ? (
            <QuickLookPreview row={hoveredRow} />
          ) : selectedId ? (
            // `key` forces a fresh RecordDetail (and fresh internal `form`
            // state) per record. Without it, clicking a different row while
            // an unsaved edit was still in `form` on the old one leaked that
            // edit's values onto the newly-selected record — the "clicking
            // another entry keeps showing the previous one" bug.
            <RecordDetail
              key={selectedId}
              invoiceId={selectedId}
              onClose={() => {
                setSelectedId(null);
                setHoveredId(null);
              }}
            />
          ) : summaryQuery.data ? (
            <Overview summaries={summaryQuery.data} />
          ) : (
            <div className="space-y-4">
              <Skeleton className="h-40 w-full rounded-xl" />
              <Skeleton className="h-6 w-1/3" />
            </div>
          )}
        </section>
      </div>
    </TooltipProvider>
  );
}
