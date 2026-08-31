import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Camera, CheckCircle2, ChevronLeft, ChevronRight, ExternalLink, FileText, Mail, MessageCircle,
  Send, Slack, Sparkles, UploadCloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { PageHeader, StatusBadge } from "@/components/app-shell";
import { SlackFilesSheet } from "@/components/slack/slack-files-sheet";
import { EmailFilesSheet } from "@/components/email/email-files-sheet";
import { InvoiceOriginalPreview } from "@/components/invoices/invoice-original-preview";
import { BoundingBoxOverlay } from "@/components/invoices/bounding-box-overlay";
import { InvoiceThumbnail } from "@/components/records/invoice-thumbnail";
import { CameraCaptureDialog } from "@/components/scanner/camera-capture-dialog";
import {
  fetchInvoiceContent, getInvoice, getScanStatus, invoiceCategoryOptions, listInvoices, paymentMethodLabel,
  scanInvoice, sendToAccounting, updateInvoice, validateInvoice, type Invoice, type InvoiceStatus, type PaymentMethod,
} from "@/lib/invoice-service";
import { toast } from "sonner";

/** Saved Records' own "no category set" label — duplicated here (Records
 * defines the same constant locally, matching this codebase's existing
 * "each page owns its own small display constants" convention) rather than
 * a shared import, so the two pages stay independently editable. */
const UNCATEGORIZED = "Uncategorized";

/** How many scanned invoices the bottom table shows per page — a fixed,
 * page-at-a-time browse rather than one growing/scrolling list, so this
 * table stays a fixed, predictable height no matter how many documents
 * have ever been scanned. */
const PAGE_SIZE = 25;

/** `?invoice=<id>` deep-links straight to one invoice — how Saved Records'
 * rows open back into the Scanner, since there is otherwise no way to view
 * an already-saved invoice's full detail (fields, line items, document
 * preview) outside this page. */
const scannerSearchSchema = z.object({
  invoice: z.string().uuid().optional(),
});

export const Route = createFileRoute("/app/scanner")({
  head: () => ({
    meta: [
      { title: "AI Invoice Scanner — FinPilot AI" },
      { name: "description", content: "Drop a PDF, photo or WhatsApp screenshot and let AI extract every invoice field." },
      { property: "og:title", content: "AI Invoice Scanner — FinPilot AI" },
      { property: "og:description", content: "AI-extracted invoice fields in seconds." },
    ],
  }),
  validateSearch: scannerSearchSchema,
  component: Scanner,
});

const STATUS_LABELS: Record<InvoiceStatus, string> = {
  processed: "Processed",
  needs_review: "Needs Review",
  needs_review_high_priority: "Needs Review · Priority",
  validated: "Validated",
  sent_to_accounting: "Sent to Accounting",
  rejected: "Rejected",
};

//: Never inferred by the rules engine (see PaymentMethod's own backend
//: docstring) — a fixed two-option pick, so a tag/pill picker fits better
//: than a dropdown built for an open-ended list.
const PAYMENT_METHOD_OPTIONS: PaymentMethod[] = ["bank", "cash"];

/** Amounts are shown with the currency the rules engine actually detected
 * on this document (extracted_fields.currency), never a default: a PKR
 * invoice and a USD invoice must not both render as a bare number, and
 * neither may be labelled with the other's currency. When no currency
 * evidence was found the number is shown plainly rather than guessed at. */
function formatAmount(n: number | null, currency?: string | null): string {
  if (n === null) return "—";
  const amount = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${currency} ${amount}` : amount;
}

/** What to show as this row's thumbnail-dialog title / hover-preview alt
 * text — same fallback order Saved Records' own `particulars()` uses, so a
 * row with no extracted vendor/invoice number still gets a sensible label
 * instead of a blank one. */
function thumbnailLabel(inv: Pick<Invoice, "invoice_number" | "vendor_name"> & { filename?: string | null }): string {
  return inv.invoice_number || inv.vendor_name || inv.filename || "Scanned document";
}

/** ISO code the engine detected for this document, or null when it found no
 * currency evidence at all. */
function currencyOf(invoice: Invoice | undefined): string | null {
  const detected = invoice?.extracted_fields?.["currency"]?.value;
  return typeof detected === "string" && detected ? detected : null;
}

/** Suffix for a money field's label, e.g. "Subtotal (PKR)". Empty when no
 * currency was detected, so the label simply reads "Subtotal". */
function currencySuffix(currency: string | null): string {
  return currency ? ` (${currency})` : "";
}

function confidenceTone(confidence: number): string {
  if (confidence >= 0.75) return "text-success";
  if (confidence >= 0.4) return "text-warning-foreground dark:text-warning";
  return "text-destructive";
}

function confidenceDotTone(confidence: number): string {
  if (confidence >= 0.75) return "bg-success";
  if (confidence >= 0.4) return "bg-warning";
  return "bg-destructive";
}

/** Compact version of the same auto-filled/confidence signal Field shows,
 * for the summary row where there's no room for a text badge. Renders
 * nothing at all when the field was never found — an absent dot for an
 * absent value, not a red dot implying a failed attempt. */
function ConfidenceDot({ confidence }: { confidence?: number | undefined }) {
  if (confidence === undefined) return null;
  return (
    <span
      className={`inline-block h-1.5 w-1.5 rounded-full ${confidenceDotTone(confidence)}`}
      title={`Auto-filled — ${Math.round(confidence * 100)}% confidence`}
    />
  );
}

interface FormState {
  vendor_name: string;
  invoice_number: string;
  invoice_date: string;
  ntn: string;
  subtotal: string;
  tax_rate: string;
  tax_amount: string;
  total: string;
  payment_method: PaymentMethod | "";
  /** Saved Records' cashbook category — settable right here at review time,
   * not only later on the Records page. "" means unset ("Uncategorized"). */
  category: string;
  items: { description: string; qty: string; rate: string; amount: string }[];
}

function toForm(invoice: Invoice): FormState {
  return {
    vendor_name: invoice.vendor_name ?? "",
    invoice_number: invoice.invoice_number ?? "",
    invoice_date: invoice.invoice_date ?? "",
    ntn: invoice.ntn ?? "",
    subtotal: invoice.subtotal?.toString() ?? "",
    tax_rate: invoice.tax_rate != null ? (invoice.tax_rate * 100).toFixed(2) : "",
    tax_amount: invoice.tax_amount?.toString() ?? "",
    total: invoice.total?.toString() ?? "",
    payment_method: invoice.payment_method ?? "",
    category: invoice.category ?? "",
    items: invoice.items.map((item) => ({
      description: item.description ?? "",
      qty: item.qty?.toString() ?? "",
      rate: item.rate?.toString() ?? "",
      amount: item.amount?.toString() ?? "",
    })),
  };
}

function numOrNull(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isNaN(n) ? null : n;
}

/** `confidence` present = the rules engine auto-filled this field and is
 * this sure about it. `confidence` absent (undefined) = the document
 * genuinely didn't have this field, or the engine couldn't confidently read
 * it — the field is blank for the human to fill in, not a failure to hide. */
function Field({
  label, value, onChange, confidence, onActivate, onDeactivate,
}: {
  label: string; value: string; onChange: (v: string) => void; confidence?: number | undefined;
  onActivate?: () => void; onDeactivate?: () => void;
}) {
  const autoFilled = confidence !== undefined;
  return (
    <div className="space-y-1.5" onMouseEnter={onActivate} onMouseLeave={onDeactivate} onFocus={onActivate} onBlur={onDeactivate}>
      <div className="flex items-center justify-between">
        <Label className="text-xs text-muted-foreground">{label}</Label>
        {autoFilled ? (
          <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${confidenceTone(confidence)}`}>
            <Sparkles className="h-2.5 w-2.5" /> {Math.round(confidence * 100)}%
          </span>
        ) : (
          <span className="text-[10px] font-medium text-muted-foreground">Not found — fill manually</span>
        )}
      </div>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`rounded-xl ${autoFilled ? "border-success/30 bg-success/5" : ""}`}
      />
    </div>
  );
}

/** Human-readable labels for Invoice.extracted_fields' keys. Kept beside the
 * component that renders them rather than in the API client, so adding a
 * newly-detected field server-side never needs a matching frontend change to
 * *show* it — an unmapped key falls back to its own de-underscored name. */
const DETECTED_FIELD_LABELS: Record<string, string> = {
  customer_name: "Customer",
  currency: "Currency",
  document_type: "Document Type",
  payment_status: "Payment Status",
  city: "City",
  country: "Country",
};

function titleCase(key: string): string {
  return key.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/** The read-only half of the extraction: everything the rules engine
 * detected beyond the eight correctable header fields. Renders nothing at
 * all when no such field was found — an empty "Detected Details" heading
 * over a blank space reads as broken, whereas its absence just means this
 * document had no extra evidence on it. */
function DetectedDetails({ fields }: { fields: Invoice["extracted_fields"] }) {
  const entries = Object.entries(fields ?? {});
  if (entries.length === 0) return null;

  return (
    <>
      <h4 className="mt-6 text-sm font-semibold">Detected Details</h4>
      <p className="text-xs text-muted-foreground">Read-only — detected from the document, not editable here</p>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {entries.map(([key, field]) => (
          <div key={key} className="rounded-xl border bg-muted/30 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">
                {DETECTED_FIELD_LABELS[key] ?? titleCase(key)}
              </span>
              {field.status === "UNCERTAIN" && (
                <span className="shrink-0 text-[10px] font-medium text-warning">Uncertain</span>
              )}
            </div>
            <p className="truncate text-sm font-medium" title={String(field.value)}>{String(field.value)}</p>
          </div>
        ))}
      </div>
    </>
  );
}

/** Every other labelled field the rules engine found on the document —
 * vendor-specific things this pipeline has no canonical name for
 * ("Shipping & Handling", "PO #", "Payment Method"). Shows the document's
 * own printed label, not a normalised one, so what's on screen matches what
 * the user is looking at on the page. Hovering highlights the value's
 * source region, same as a canonical field. */
function OtherFields({
  fields, onActivate, onDeactivate,
}: {
  fields: Invoice["dynamic_fields"];
  onActivate: (f: Invoice["dynamic_fields"][number]) => void;
  onDeactivate: () => void;
}) {
  if (!fields || fields.length === 0) return null;

  return (
    <>
      <h4 className="mt-6 text-sm font-semibold">Other Fields on This Document</h4>
      <p className="text-xs text-muted-foreground">
        Detected automatically — hover to see where each came from
      </p>
      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {fields.map((f) => (
          <div
            key={f.key}
            className="rounded-xl border bg-muted/30 px-3 py-2 transition-colors hover:border-primary/40"
            onMouseEnter={() => onActivate(f)}
            onMouseLeave={onDeactivate}
          >
            <span className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{f.label}</span>
            <p className="truncate text-sm font-medium" title={f.value}>{f.value}</p>
          </div>
        ))}
      </div>
    </>
  );
}

function Scanner() {
  const queryClient = useQueryClient();
  const search = Route.useSearch();
  const [dragging, setDragging] = useState(false);
  const [slackSheetOpen, setSlackSheetOpen] = useState(false);
  const [emailSheetOpen, setEmailSheetOpen] = useState(false);
  const [cameraDialogOpen, setCameraDialogOpen] = useState(false);
  // Seeded from ?invoice=<id> (Saved Records' row links land here) so the
  // deep link opens straight into that invoice instead of an empty panel.
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<string | null>(search.invoice ?? null);
  const [statusFilter, setStatusFilter] = useState<InvoiceStatus | "all">("all");
  const [form, setForm] = useState<FormState | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  //: Which field's source region is highlighted on the document preview,
  //: and which page that preview is showing.
  const [activeField, setActiveField] = useState<{ page: number; bbox: [number, number, number, number] } | null>(null);
  const [currentPage, setCurrentPage] = useState(0);
  // Which page of the bottom "Scanned Invoices" table is showing — a fixed
  // 25-at-a-time browse (PAGE_SIZE), never one growing/scrolling list.
  const [listPage, setListPage] = useState(0);

  const listQuery = useQuery({
    queryKey: ["invoices", statusFilter, listPage],
    queryFn: () => listInvoices({ status: statusFilter, skip: listPage * PAGE_SIZE, limit: PAGE_SIZE }),
  });

  // Same cache Saved Records' own Category select already populates
  // (queryKey: ["invoice-options"]) — the suggestion list is identical on
  // both pages, so scanning here and switching to Records never shows two
  // different sets of categories to pick from.
  const categoryOptionsQuery = useQuery({ queryKey: ["invoice-options"], queryFn: invoiceCategoryOptions });

  const detailQuery = useQuery({
    queryKey: ["invoice", selectedInvoiceId],
    queryFn: () => getInvoice(selectedInvoiceId as string),
    enabled: Boolean(selectedInvoiceId),
    refetchOnWindowFocus: false,
  });

  // Re-derives the editable form whenever a different invoice loads (or the
  // server confirms a save) — refetchOnWindowFocus is off above precisely so
  // this never fires mid-edit just because the tab regained focus.
  useEffect(() => {
    setForm(detailQuery.data ? toForm(detailQuery.data) : null);
  }, [detailQuery.data]);

  const scanMutation = useMutation({
    mutationFn: (file: File) => scanInvoice(file),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      if (result.invoice_id) setSelectedInvoiceId(result.invoice_id);
      const status = result.invoice?.status;
      if (status === "processed") {
        toast.success("Invoice extracted and processed automatically");
      } else {
        toast(
          "Invoice scanned — the AI wasn't fully confident on every field, so it's waiting for a quick review.",
        );
      }
      // Document Preprocessing (docs/invoice-ocr-plan.md §7) — the photo
      // confidently contained more than one receipt, each already saved as
      // its own invoice, not just noted somewhere inside this one.
      if (result.additional_invoice_ids.length > 0) {
        const count = result.additional_invoice_ids.length;
        toast(
          `Found ${count} more document${count === 1 ? "" : "s"} in this photo — each was scanned and saved as its own invoice.`,
        );
      }
    },
    onError: (error: Error) => toast.error(error.message || "Could not scan this file"),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateInvoice>[1]) =>
      updateInvoice(selectedInvoiceId as string, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not save changes"),
  });

  const validateMutation = useMutation({
    mutationFn: () => validateInvoice(selectedInvoiceId as string),
    onSuccess: () => {
      toast.success("Invoice validated");
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not validate this invoice"),
  });

  const sendMutation = useMutation({
    mutationFn: () => sendToAccounting(selectedInvoiceId as string),
    onSuccess: () => {
      toast.success("Posted to accounting ledger");
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not send this invoice to accounting"),
  });

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    scanMutation.mutate(file);
  };

  /** Shared by Save, Validate, and Send — the form's current values as the
   * PUT payload updateInvoice expects. */
  const buildUpdatePayload = (state: FormState): Parameters<typeof updateInvoice>[1] => ({
    vendor_name: state.vendor_name || null,
    invoice_number: state.invoice_number || null,
    invoice_date: state.invoice_date || null,
    ntn: state.ntn || null,
    subtotal: numOrNull(state.subtotal),
    tax_rate: state.tax_rate ? numOrNull(state.tax_rate)! / 100 : null,
    tax_amount: numOrNull(state.tax_amount),
    total: numOrNull(state.total),
    payment_method: state.payment_method || null,
    category: state.category || null,
    items: state.items.map((item) => ({
      description: item.description || null,
      qty: numOrNull(item.qty),
      rate: numOrNull(item.rate),
      amount: numOrNull(item.amount),
    })),
  });

  const handleSave = () => {
    if (!form) return;
    updateMutation.mutate(buildUpdatePayload(form), {
      onSuccess: () => toast.success("Changes saved"),
    });
  };

  /** Validate and Send both persist the form's current values first, then
   * flip status — otherwise an edit made right before either button (the
   * payment method select in particular, since nothing else prompts a
   * save) is silently discarded: those endpoints only flip status on
   * whatever is already saved in the database, not on what's currently
   * showing in the form. */
  const handleValidate = async () => {
    if (!form) return;
    try {
      await updateMutation.mutateAsync(buildUpdatePayload(form));
    } catch {
      return; // updateMutation's own onError already toasted why; don't validate stale/unsaved data.
    }
    validateMutation.mutate();
  };

  const handleSend = async () => {
    if (!form) return;
    try {
      await updateMutation.mutateAsync(buildUpdatePayload(form));
    } catch {
      return; // Same reasoning as handleValidate above.
    }
    sendMutation.mutate();
  };

  const updateItem = (index: number, field: "description" | "qty" | "rate" | "amount", value: string) => {
    setForm((prev) => {
      if (!prev) return prev;
      const items = prev.items.map((item, i) => (i === index ? { ...item, [field]: value } : item));
      return { ...prev, items };
    });
  };

  const invoice = detailQuery.data;
  const isBusy = scanMutation.isPending;
  const currency = currencyOf(invoice);

  /** Every field with a known location on the page — the canonical header
   * fields plus the generically-discovered ones. Drawn faintly on the
   * preview so the grounding is visible without hovering. */
  const allLocatedFields = [
    ...Object.values(invoice?.field_locations ?? {}),
    ...(invoice?.dynamic_fields ?? [])
      .filter((f) => f.bbox !== null && f.page !== null)
      .map((f) => ({ page: f.page as number, bbox: f.bbox as [number, number, number, number] })),
  ];

  /** Hover/focus handlers that highlight where a field was read from on the
   * document preview. Returns {} for a field with no recorded location, so
   * the field simply has no hover behaviour rather than highlighting the
   * wrong region. */
  const locate = (fieldName: string) => {
    const loc = invoice?.field_locations?.[fieldName];
    if (!loc) return {};
    return {
      onActivate: () => {
        setActiveField(loc);
        setCurrentPage(loc.page);
      },
      onDeactivate: () => setActiveField(null),
    };
  };

  return (
    <TooltipProvider>
      <PageHeader
        title="AI Invoice Scanner"
        subtitle="Upload any document — FinPilot reads vendor, items, tax and totals automatically."
        actions={
          invoice && (
            <span className={`inline-flex items-center gap-1.5 rounded-full bg-success/12 px-3 py-1.5 text-xs font-semibold ${confidenceTone(invoice.document_confidence)}`}>
              <Sparkles className="h-3.5 w-3.5" /> {Math.round(invoice.document_confidence * 100)}% extraction confidence
            </span>
          )
        }
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="flex flex-col gap-4">
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff"
            className="hidden"
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              handleFiles(e.dataTransfer.files);
            }}
            className={`surface grid place-items-center border-2 border-dashed p-10 text-center transition-colors ${
              dragging ? "border-primary bg-primary/5" : "border-border"
            }`}
          >
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
              <UploadCloud className="h-6 w-6" />
            </span>
            <h3 className="mt-4 font-display text-lg font-semibold">Drag &amp; drop your invoice</h3>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">
              Supports scanned images, PDFs and WhatsApp screenshots up to 20 MB — or pull a document
              straight from a connected app.
            </p>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <Button
                variant="outline" className="gap-2 rounded-xl"
                onClick={() => fileInputRef.current?.click()}
                disabled={isBusy}
              >
                <FileText className="h-4 w-4" /> {isBusy ? "Scanning…" : "Browse files"}
              </Button>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={() => setCameraDialogOpen(true)}>
                <Camera className="h-4 w-4" /> Camera
              </Button>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={() => toast("Import from WhatsApp isn't wired up yet — save the screenshot and use Browse files")}>
                <MessageCircle className="h-4 w-4" /> WhatsApp
              </Button>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={() => setSlackSheetOpen(true)}>
                <Slack className="h-4 w-4" /> Slack
              </Button>
              <Button variant="outline" className="gap-2 rounded-xl" onClick={() => setEmailSheetOpen(true)}>
                <Mail className="h-4 w-4" /> Email
              </Button>
            </div>
            {isBusy && <Progress value={70} className="mt-4 h-1.5 w-full max-w-xs" />}
          </div>

          <SlackFilesSheet open={slackSheetOpen} onOpenChange={setSlackSheetOpen} />
          <EmailFilesSheet open={emailSheetOpen} onOpenChange={setEmailSheetOpen} />
          <CameraCaptureDialog
            open={cameraDialogOpen}
            onOpenChange={setCameraDialogOpen}
            onConfirm={(file, category, paymentMethod) => {
              scanMutation.mutate(file, {
                onSuccess: (result) => {
                  // The scan itself already shows its own success/needs-review toast
                  // via scanMutation's existing onSuccess (unchanged) — this only
                  // patches on the two fields the camera confirm screen collected,
                  // the same "create, then patch" shape Saved Records' own inline
                  // editors already use for these exact two fields.
                  if (result.invoice_id && (category || paymentMethod)) {
                    updateInvoice(result.invoice_id, { category, payment_method: paymentMethod }).then(() => {
                      queryClient.invalidateQueries({ queryKey: ["invoice", result.invoice_id] });
                      queryClient.invalidateQueries({ queryKey: ["invoices"] });
                    });
                  }
                },
              });
            }}
          />

          <section className="surface p-5">
            {invoice ? (
              <>
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold">{invoice.filename}</h3>
                    <p className="text-xs text-muted-foreground">
                      {(invoice.size / 1024 / 1024).toFixed(2)} MB · scanned {new Date(invoice.created_at).toLocaleString()}
                    </p>
                  </div>
                  <StatusBadge status={STATUS_LABELS[invoice.status]} />
                </div>
                {invoice.review_flags.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {invoice.review_flags.map((flag) => (
                      <span
                        key={flag}
                        className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning-foreground dark:text-warning"
                      >
                        <AlertTriangle className="h-3 w-3" /> {flag.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                )}
                {invoice.page_dimensions.length > 0 ? (
                  <div className="mt-3">
                    <BoundingBoxOverlay
                      invoiceId={invoice.id}
                      filename={invoice.filename}
                      page={currentPage}
                      pageDimensions={invoice.page_dimensions[currentPage]}
                      activeField={activeField}
                      fields={allLocatedFields}
                    />
                    {invoice.page_dimensions.length > 1 && (
                      <div className="mt-2 flex items-center justify-center gap-3 text-xs text-muted-foreground">
                        <Button
                          variant="ghost" size="icon" className="h-7 w-7 rounded-lg"
                          disabled={currentPage === 0}
                          onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </Button>
                        Page {currentPage + 1} of {invoice.page_dimensions.length}
                        <Button
                          variant="ghost" size="icon" className="h-7 w-7 rounded-lg"
                          disabled={currentPage === invoice.page_dimensions.length - 1}
                          onClick={() => setCurrentPage((p) => Math.min(invoice.page_dimensions.length - 1, p + 1))}
                        >
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                    <p className="mt-2 text-center text-[11px] text-muted-foreground">
                      Hover a field on the right to highlight where it came from
                    </p>
                  </div>
                ) : (
                  // Documents scanned before grounding data existed, and any
                  // whose extraction produced no page dimensions, still get
                  // the plain preview rather than an empty frame.
                  <InvoiceOriginalPreview invoiceId={invoice.id} mimetype={invoice.mimetype} filename={invoice.filename} />
                )}
              </>
            ) : (
              <div className="grid aspect-[4/3] place-items-center text-center text-sm text-muted-foreground">
                <div>
                  <p>Upload a document or select one from the list below</p>
                  <p className="mt-1 text-xs">Extracted fields will appear here for review</p>
                </div>
              </div>
            )}
          </section>
        </div>

        <section className="surface p-5 sm:p-6">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
            <div className="min-w-0">
              <h3 className="truncate text-base font-semibold">Extracted Information</h3>
              <p className="text-xs text-muted-foreground">Review and edit before posting to accounting</p>
            </div>
            {invoice?.status === "processed" && (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-success">
                <CheckCircle2 className="h-3.5 w-3.5" /> AI verified
              </span>
            )}
          </div>

          {!invoice || !form ? (
            <p className="mt-8 text-center text-sm text-muted-foreground">
              {selectedInvoiceId && detailQuery.isPending ? "Loading…" : "Nothing selected yet"}
            </p>
          ) : (
            <>
              <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Vendor Name" value={form.vendor_name} onChange={(v) => setForm({ ...form, vendor_name: v })} confidence={invoice.field_confidence['vendor_name']} {...locate('vendor_name')} />
                <Field label="Invoice Number" value={form.invoice_number} onChange={(v) => setForm({ ...form, invoice_number: v })} confidence={invoice.field_confidence['invoice_number']} {...locate('invoice_number')} />
                <Field label="Invoice Date" value={form.invoice_date} onChange={(v) => setForm({ ...form, invoice_date: v })} confidence={invoice.field_confidence['invoice_date']} {...locate('invoice_date')} />
                <Field label="Vendor NTN" value={form.ntn} onChange={(v) => setForm({ ...form, ntn: v })} confidence={invoice.field_confidence['ntn']} {...locate('ntn')} />
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs text-muted-foreground">Payment Method</Label>
                    <span className="text-[10px] font-medium text-muted-foreground">Select manually</span>
                  </div>
                  <div className="flex gap-2">
                    {PAYMENT_METHOD_OPTIONS.map((method) => (
                      <button
                        key={method}
                        type="button"
                        aria-pressed={form.payment_method === method}
                        onClick={() => setForm({ ...form, payment_method: method })}
                        className={`flex-1 rounded-full border px-4 py-2 text-sm font-medium transition-colors ${
                          form.payment_method === method
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
                        }`}
                      >
                        {paymentMethodLabel(method)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs text-muted-foreground">Category</Label>
                    <span className="text-[10px] font-medium text-muted-foreground">Select manually</span>
                  </div>
                  <Select
                    value={form.category || UNCATEGORIZED}
                    onValueChange={(v) => setForm({ ...form, category: v === UNCATEGORIZED ? "" : v })}
                  >
                    <SelectTrigger className="rounded-xl"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={UNCATEGORIZED}>{UNCATEGORIZED}</SelectItem>
                      {(categoryOptionsQuery.data?.categories ?? []).map((c) => (
                        <SelectItem key={c} value={c}>{c}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <DetectedDetails fields={invoice.extracted_fields} />
              <OtherFields
                fields={invoice.dynamic_fields}
                onActivate={(f) => {
                  if (!f.bbox || f.page === null) return;
                  setActiveField({ page: f.page, bbox: f.bbox });
                  setCurrentPage(f.page);
                }}
                onDeactivate={() => setActiveField(null)}
              />

              <h4 className="mt-6 text-sm font-semibold">Line Items</h4>
              <div className="mt-2 overflow-x-auto rounded-xl border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Description</TableHead>
                      <TableHead className="w-20">Qty</TableHead>
                      <TableHead className="w-28">Rate{currencySuffix(currency)}</TableHead>
                      <TableHead className="text-right">Amount{currencySuffix(currency)}</TableHead>
                      <TableHead className="w-10" title="Does qty × rate match the amount on the document?" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {form.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                          No line items were found on this document
                        </TableCell>
                      </TableRow>
                    ) : (
                      form.items.map((item, index) => {
                        const original = invoice.items[index];
                        return (
                        <TableRow key={index}>
                          <TableCell>
                            <Input value={item.description} onChange={(e) => updateItem(index, "description", e.target.value)} className="h-9 rounded-lg" />
                          </TableCell>
                          <TableCell>
                            <Input value={item.qty} onChange={(e) => updateItem(index, "qty", e.target.value)} className="h-9 w-16 rounded-lg" />
                          </TableCell>
                          <TableCell>
                            <Input value={item.rate} onChange={(e) => updateItem(index, "rate", e.target.value)} className="h-9 w-24 rounded-lg" />
                          </TableCell>
                          <TableCell>
                            <Input value={item.amount} onChange={(e) => updateItem(index, "amount", e.target.value)} className="h-9 rounded-lg text-right" />
                          </TableCell>
                          <TableCell className="px-2">
                            {original?.arithmetic_check === "pass" && (
                              <CheckCircle2 className="h-4 w-4 text-success" aria-label="qty × rate matches the amount" />
                            )}
                            {original?.arithmetic_check === "fail" && (
                              <AlertTriangle className="h-4 w-4 text-destructive" aria-label="qty × rate does not match the amount — check this row" />
                            )}
                          </TableCell>
                        </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </div>

              <div className="mt-5 space-y-2 rounded-xl bg-muted/50 p-4 text-sm">
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <Label className="text-xs">Subtotal{currencySuffix(currency)}</Label>
                    <ConfidenceDot confidence={invoice.field_confidence['subtotal']} />
                  </span>
                  <Input value={form.subtotal} onChange={(e) => setForm({ ...form, subtotal: e.target.value })} className="h-8 w-32 rounded-lg text-right" />
                </div>
                <div className="flex items-center justify-between text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <Label className="text-xs">Sales Tax (%)</Label>
                    <ConfidenceDot confidence={invoice.field_confidence['tax_rate']} />
                  </span>
                  <Input value={form.tax_rate} onChange={(e) => setForm({ ...form, tax_rate: e.target.value })} className="h-8 w-32 rounded-lg text-right" />
                </div>
                <div className="flex items-center justify-between border-t pt-2">
                  <span className="flex items-center gap-1.5">
                    <Label className="font-display text-base font-bold">Total{currencySuffix(currency)}</Label>
                    <ConfidenceDot confidence={invoice.field_confidence['total']} />
                  </span>
                  <Input value={form.total} onChange={(e) => setForm({ ...form, total: e.target.value })} className="h-9 w-32 rounded-lg text-right font-bold" />
                </div>
              </div>

              <div className="mt-5 flex flex-wrap gap-2">
                <Button
                  variant="outline" className="rounded-xl"
                  disabled={validateMutation.isPending || updateMutation.isPending || invoice.status === "validated" || invoice.status === "sent_to_accounting"}
                  onClick={() => handleValidate()}
                >
                  {invoice.status === "validated" || invoice.status === "sent_to_accounting" ? "Validated" : "Validate"}
                </Button>
                <Button variant="outline" className="rounded-xl" disabled={updateMutation.isPending} onClick={handleSave}>
                  {updateMutation.isPending ? "Saving…" : "Save"}
                </Button>
                <Button
                  className="gap-2 rounded-xl"
                  disabled={sendMutation.isPending || updateMutation.isPending || invoice.status === "sent_to_accounting" || invoice.status === "needs_review" || invoice.status === "needs_review_high_priority"}
                  onClick={() => handleSend()}
                >
                  <Send className="h-4 w-4" /> {invoice.status === "sent_to_accounting" ? "Sent to Accounting" : "Send to Accounting"}
                </Button>
              </div>
            </>
          )}
        </section>
      </div>

      <section className="surface mt-6 overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <div>
            <h3 className="text-base font-semibold">Scanned Invoices</h3>
            <p className="text-xs text-muted-foreground">
              {listQuery.data ? `${listQuery.data.total} total` : "Loading…"}
            </p>
          </div>
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              setStatusFilter(v as InvoiceStatus | "all");
              setListPage(0); // a filter change can shrink the result set below the page we were on
            }}
          >
            <SelectTrigger className="w-48 rounded-xl">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </header>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-14" />
                <TableHead>Invoice No</TableHead>
                <TableHead>Vendor</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {listQuery.isPending ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={7}><Skeleton className="h-6 w-full" /></TableCell>
                  </TableRow>
                ))
              ) : listQuery.data?.invoices.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-sm text-muted-foreground py-8">
                    {listPage > 0 ? "No more invoices on this page" : "No invoices scanned yet — drop a file above to get started"}
                  </TableCell>
                </TableRow>
              ) : (
                // Flat, one row per document, newest scan first (the
                // backend's own default ordering) — deliberately never
                // grouped by category the way Saved Records groups its own
                // list; this table stays a plain scan log.
                listQuery.data?.invoices.map((inv) => (
                  <TableRow
                    key={inv.id}
                    className={`cursor-pointer hover:bg-muted/50 ${inv.id === selectedInvoiceId ? "bg-muted/40" : ""}`}
                    onClick={() => setSelectedInvoiceId(inv.id)}
                  >
                    <TableCell className="px-2">
                      {/* Same component Saved Records uses for its own row
                       * thumbnail: hover for a larger preview, click for a
                       * centered lightbox with a close button and dimmed
                       * backdrop — smallHoverPreview=true since, unlike
                       * Records' left pane, nothing here already live-updates
                       * a big preview panel on row hover. */}
                      <InvoiceThumbnail invoiceId={inv.id} label={thumbnailLabel(inv)} smallHoverPreview />
                    </TableCell>
                    <TableCell className="font-medium">{inv.invoice_number ?? "—"}</TableCell>
                    <TableCell>{inv.vendor_name ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{inv.invoice_date ?? "—"}</TableCell>
                    <TableCell className="text-right font-medium">{formatAmount(inv.total)}</TableCell>
                    <TableCell>
                      <StatusBadge status={STATUS_LABELS[inv.status]} />
                    </TableCell>
                    <TableCell className="px-2">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Link
                            to="/app/records"
                            search={{ invoice: inv.id }}
                            onClick={(e) => e.stopPropagation()}
                            className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            aria-label="Open in Saved Records"
                          >
                            <ExternalLink className="h-4 w-4" />
                          </Link>
                        </TooltipTrigger>
                        <TooltipContent>Open in Saved Records</TooltipContent>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {listQuery.data && listQuery.data.total > 0 && (
          <div className="flex items-center justify-between gap-3 border-t px-5 py-3 text-xs text-muted-foreground">
            <span>
              Showing {listPage * PAGE_SIZE + 1}
              –{Math.min(listPage * PAGE_SIZE + listQuery.data.invoices.length, listQuery.data.total)} of{" "}
              {listQuery.data.total}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm" className="gap-1 rounded-lg"
                disabled={listPage === 0}
                onClick={() => setListPage((p) => Math.max(0, p - 1))}
              >
                <ChevronLeft className="h-3.5 w-3.5" /> Previous
              </Button>
              <span className="font-medium">
                Page {listPage + 1} of {Math.max(1, Math.ceil(listQuery.data.total / PAGE_SIZE))}
              </span>
              <Button
                variant="outline" size="sm" className="gap-1 rounded-lg"
                disabled={(listPage + 1) * PAGE_SIZE >= listQuery.data.total}
                onClick={() => setListPage((p) => p + 1)}
              >
                Next <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </section>
    </TooltipProvider>
  );
}
