import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, FileText, Mail, MessageCircle, Send, Slack,
  Sparkles, UploadCloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, StatusBadge } from "@/components/app-shell";
import { ChartCard, HorizontalBars, VerticalBars } from "@/components/charts";
import { SlackFilesSheet } from "@/components/slack/slack-files-sheet";
import { EmailFilesSheet } from "@/components/email/email-files-sheet";
import { InvoiceOriginalPreview } from "@/components/invoices/invoice-original-preview";
import { BoundingBoxOverlay } from "@/components/invoices/bounding-box-overlay";
import {
  ConfidenceDot, DetectedDetails, Field, OtherFields, confidenceTone, currencyOf, currencySuffix, formatAmount,
} from "@/components/invoices/sale-extraction-fields";
import {
  getInvoice, getSalesSummary, listScannedSales, scanSalesInvoice, sendToAccounting,
  updateInvoice, validateInvoice, type Invoice, type InvoiceStatus,
} from "@/lib/invoice-service";
import { toast } from "sonner";

/** `?invoice=<id>` deep-links straight to one scanned sale — same pattern
 *  as the Scanner's own search schema, for consistency if something ever
 *  needs to link here directly (a future Records-style entry). */
const revenueSearchSchema = z.object({
  invoice: z.string().uuid().optional(),
});

export const Route = createFileRoute("/app/revenue")({
  head: () => ({
    meta: [
      { title: "Revenue Manager — FinPilot AI" },
      { name: "description", content: "Capture sales invoices and track every rupee that comes in." },
      { property: "og:title", content: "Revenue Manager — FinPilot AI" },
      { property: "og:description", content: "Scan sales documents and watch revenue add up automatically." },
    ],
  }),
  validateSearch: revenueSearchSchema,
  component: RevenueManager,
});

const STATUS_LABELS: Record<InvoiceStatus, string> = {
  processed: "Processed",
  needs_review: "Needs Review",
  needs_review_high_priority: "Needs Review · Priority",
  validated: "Validated",
  sent_to_accounting: "Posted",
  rejected: "Rejected",
};

interface FormState {
  customer_name: string;
  invoice_number: string;
  invoice_date: string;
  ntn: string;
  subtotal: string;
  tax_rate: string;
  tax_amount: string;
  total: string;
  items: { description: string; qty: string; rate: string; amount: string }[];
}

function toForm(invoice: Invoice): FormState {
  return {
    customer_name: invoice.customer_name ?? "",
    invoice_number: invoice.invoice_number ?? "",
    invoice_date: invoice.invoice_date ?? "",
    ntn: invoice.ntn ?? "",
    subtotal: invoice.subtotal?.toString() ?? "",
    tax_rate: invoice.tax_rate != null ? (invoice.tax_rate * 100).toFixed(2) : "",
    tax_amount: invoice.tax_amount?.toString() ?? "",
    total: invoice.total?.toString() ?? "",
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

function money(n: number): string {
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

function RevenueManager() {
  const queryClient = useQueryClient();
  const search = Route.useSearch();
  const [dragging, setDragging] = useState(false);
  const [slackSheetOpen, setSlackSheetOpen] = useState(false);
  const [emailSheetOpen, setEmailSheetOpen] = useState(false);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState<string | null>(search.invoice ?? null);
  const [statusFilter, setStatusFilter] = useState<InvoiceStatus | "all">("all");
  const [form, setForm] = useState<FormState | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [activeField, setActiveField] = useState<{ page: number; bbox: [number, number, number, number] } | null>(null);
  const [currentPage, setCurrentPage] = useState(0);

  const listQuery = useQuery({
    queryKey: ["scanned-sales", statusFilter],
    queryFn: () => listScannedSales({ status: statusFilter, limit: 20 }),
  });

  const summaryQuery = useQuery({
    queryKey: ["scanned-sales", "summary"],
    queryFn: getSalesSummary,
  });

  // getInvoice is the same detail endpoint the purchase Scanner uses — it
  // is type-agnostic already (design spec §6.5), so a scanned sale's id
  // resolves through it with no change on that endpoint's part.
  const detailQuery = useQuery({
    queryKey: ["invoice", selectedInvoiceId],
    queryFn: () => getInvoice(selectedInvoiceId as string),
    enabled: Boolean(selectedInvoiceId),
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    setForm(detailQuery.data ? toForm(detailQuery.data) : null);
  }, [detailQuery.data]);

  const scanMutation = useMutation({
    mutationFn: (file: File) => scanSalesInvoice(file),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["scanned-sales"] });
      if (result.invoice_id) setSelectedInvoiceId(result.invoice_id);
      const status = result.invoice?.status;
      if (status === "processed") {
        toast.success("Sale extracted and processed automatically");
      } else {
        toast("Sale scanned — the AI wasn't fully confident on every field, so it's waiting for a quick review.");
      }
    },
    onError: (error: Error) => toast.error(error.message || "Could not scan this file"),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateInvoice>[1]) =>
      updateInvoice(selectedInvoiceId as string, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["scanned-sales"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not save changes"),
  });

  const validateMutation = useMutation({
    mutationFn: () => validateInvoice(selectedInvoiceId as string),
    onSuccess: () => {
      toast.success("Sale validated");
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["scanned-sales"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not validate this sale"),
  });

  // "Post Revenue" reuses the same local-status-flip endpoint the purchase
  // side's "Send to Accounting" does — Transactions Service does not exist
  // yet (architecture report §11b), so there is no real ledger posting
  // behind either button today. Design spec §4's v1 limitations.
  const postMutation = useMutation({
    mutationFn: () => sendToAccounting(selectedInvoiceId as string),
    onSuccess: () => {
      toast.success("Posted to revenue");
      queryClient.invalidateQueries({ queryKey: ["invoice", selectedInvoiceId] });
      queryClient.invalidateQueries({ queryKey: ["scanned-sales"] });
      queryClient.invalidateQueries({ queryKey: ["scanned-sales", "summary"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not post this sale to revenue"),
  });

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    scanMutation.mutate(file);
  };

  const buildUpdatePayload = (state: FormState): Parameters<typeof updateInvoice>[1] => ({
    customer_name: state.customer_name || null,
    invoice_number: state.invoice_number || null,
    invoice_date: state.invoice_date || null,
    ntn: state.ntn || null,
    subtotal: numOrNull(state.subtotal),
    tax_rate: state.tax_rate ? numOrNull(state.tax_rate)! / 100 : null,
    tax_amount: numOrNull(state.tax_amount),
    total: numOrNull(state.total),
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

  /** Validate and Post Revenue both persist the form's current values
   *  first, then flip status — same reasoning as the Scanner's own
   *  handleValidate/handleSend: those endpoints only flip status on
   *  whatever is already saved, not on what's currently in the form. */
  const handleValidate = async () => {
    if (!form) return;
    try {
      await updateMutation.mutateAsync(buildUpdatePayload(form));
    } catch {
      return;
    }
    validateMutation.mutate();
  };

  const handlePost = async () => {
    if (!form) return;
    try {
      await updateMutation.mutateAsync(buildUpdatePayload(form));
    } catch {
      return;
    }
    postMutation.mutate();
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

  const allLocatedFields = [
    ...Object.values(invoice?.field_locations ?? {}),
    ...(invoice?.dynamic_fields ?? [])
      .filter((f) => f.bbox !== null && f.page !== null)
      .map((f) => ({ page: f.page as number, bbox: f.bbox as [number, number, number, number] })),
  ];

  /** The rules engine reports a sale's counterparty confidence/location
   *  under the `vendor_name` key regardless of which party it actually
   *  found (invoice_builder.py's own docstring) — so the Customer field's
   *  hover/confidence must read from that key even though its editable
   *  *value* is customer_name. */
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

  const monthlyChartData = (summaryQuery.data?.monthly ?? []).map((point) => ({ name: point.month, value: point.total }));
  const topCustomersChartData = (summaryQuery.data?.top_customers ?? []).map((point) => ({
    name: point.customer_name, value: point.total,
  }));

  return (
    <>
      <PageHeader
        title="Revenue Manager"
        subtitle="Capture sales invoices and track every rupee that comes in."
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
            <h3 className="mt-4 font-display text-lg font-semibold">Drag &amp; drop a sales document</h3>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">
              A sales invoice you issued, a POS receipt, a delivery challan — scanned images, PDFs
              and WhatsApp screenshots up to 20 MB, or pull one straight from a connected app.
            </p>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <Button
                variant="outline" className="gap-2 rounded-xl"
                onClick={() => fileInputRef.current?.click()}
                disabled={isBusy}
              >
                <FileText className="h-4 w-4" /> {isBusy ? "Scanning…" : "Browse files"}
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

          <SlackFilesSheet open={slackSheetOpen} onOpenChange={setSlackSheetOpen} scanTarget="sale" />
          <EmailFilesSheet open={emailSheetOpen} onOpenChange={setEmailSheetOpen} scanTarget="sale" />

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
                        className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning"
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
                  <InvoiceOriginalPreview invoiceId={invoice.id} mimetype={invoice.mimetype} filename={invoice.filename} />
                )}
              </>
            ) : (
              <div className="grid aspect-[4/3] place-items-center text-center text-sm text-muted-foreground">
                <div>
                  <p>Upload a sales document or select one from the list below</p>
                  <p className="mt-1 text-xs">Extracted fields will appear here for review</p>
                </div>
              </div>
            )}
          </section>
        </div>

        <section className="surface p-5 sm:p-6">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
            <div className="min-w-0">
              <h3 className="truncate text-base font-semibold">Extracted Sale</h3>
              <p className="text-xs text-muted-foreground">Review and edit before posting to revenue</p>
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
                <Field
                  label="Customer"
                  value={form.customer_name}
                  onChange={(v) => setForm({ ...form, customer_name: v })}
                  confidence={invoice.field_confidence["vendor_name"]}
                  {...locate("vendor_name")}
                />
                <Field label="Invoice Number" value={form.invoice_number} onChange={(v) => setForm({ ...form, invoice_number: v })} confidence={invoice.field_confidence['invoice_number']} {...locate('invoice_number')} />
                <Field label="Invoice Date" value={form.invoice_date} onChange={(v) => setForm({ ...form, invoice_date: v })} confidence={invoice.field_confidence['invoice_date']} {...locate('invoice_date')} />
                <Field label="NTN" value={form.ntn} onChange={(v) => setForm({ ...form, ntn: v })} confidence={invoice.field_confidence['ntn']} {...locate('ntn')} />
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

              <h4 className="mt-6 text-sm font-semibold">Products / Line Items</h4>
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
                  disabled={postMutation.isPending || updateMutation.isPending || invoice.status === "sent_to_accounting" || invoice.status === "needs_review" || invoice.status === "needs_review_high_priority"}
                  onClick={() => handlePost()}
                >
                  <Send className="h-4 w-4" /> {invoice.status === "sent_to_accounting" ? "Posted to Revenue" : "Post Revenue"}
                </Button>
              </div>
            </>
          )}
        </section>
      </div>

      <section className="surface mt-6 overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <div>
            <h3 className="text-base font-semibold">Scanned Sales</h3>
            <p className="text-xs text-muted-foreground">
              {listQuery.data ? `${listQuery.data.total} total` : "Loading…"}
            </p>
          </div>
          <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as InvoiceStatus | "all")}>
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
                {/* Explicit widths on the short-content columns — an HTML
                 * table's default layout otherwise stretches every column to
                 * fill the container, leaving large, empty-looking gaps
                 * (the same issue found and fixed on the Scanner page's own
                 * list). */}
                <TableHead className="w-40">Invoice No</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead className="w-28">Date</TableHead>
                <TableHead className="w-32 text-right">Amount</TableHead>
                <TableHead className="w-32">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {listQuery.isPending ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={5}><Skeleton className="h-6 w-full" /></TableCell>
                  </TableRow>
                ))
              ) : listQuery.isError ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-destructive">
                    {(listQuery.error as Error).message || "Could not load scanned sales"}
                  </TableCell>
                </TableRow>
              ) : listQuery.data?.invoices.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-sm text-muted-foreground py-8">
                    No sales scanned yet — drop a document above to get started
                  </TableCell>
                </TableRow>
              ) : (
                listQuery.data?.invoices.map((inv) => (
                  <TableRow
                    key={inv.id}
                    className={`cursor-pointer hover:bg-muted/50 ${inv.id === selectedInvoiceId ? "bg-muted/40" : ""}`}
                    onClick={() => setSelectedInvoiceId(inv.id)}
                  >
                    <TableCell className="font-medium">{inv.invoice_number ?? "—"}</TableCell>
                    <TableCell>{inv.customer_name ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{inv.invoice_date ?? "—"}</TableCell>
                    <TableCell className="text-right font-medium">{formatAmount(inv.total)}</TableCell>
                    <TableCell>
                      <StatusBadge status={STATUS_LABELS[inv.status]} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="surface p-5">
          <p className="text-xs text-muted-foreground">Revenue Captured</p>
          <p className="mt-1 font-display text-2xl font-bold">
            {summaryQuery.isPending ? <Skeleton className="h-8 w-32" /> : money(summaryQuery.data?.totals.revenue ?? 0)}
          </p>
        </div>
        <div className="surface p-5">
          <p className="text-xs text-muted-foreground">Documents Scanned</p>
          <p className="mt-1 font-display text-2xl font-bold">
            {summaryQuery.isPending ? <Skeleton className="h-8 w-16" /> : summaryQuery.data?.totals.document_count ?? 0}
          </p>
        </div>
        <div className="surface p-5">
          <p className="text-xs text-muted-foreground">Pending Review</p>
          <p className="mt-1 font-display text-2xl font-bold">
            {summaryQuery.isPending ? <Skeleton className="h-8 w-16" /> : summaryQuery.data?.totals.pending_review ?? 0}
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Monthly Revenue" subtitle="From scanned sales only">
          {summaryQuery.isPending ? (
            <Skeleton className="h-[280px] w-full" />
          ) : monthlyChartData.length === 0 ? (
            <div className="grid h-[280px] place-items-center text-center text-sm text-muted-foreground">
              No scanned sales yet
            </div>
          ) : (
            <VerticalBars data={monthlyChartData} />
          )}
        </ChartCard>
        <ChartCard title="Top Customers" subtitle="By revenue, from scanned sales">
          {summaryQuery.isPending ? (
            <Skeleton className="h-[280px] w-full" />
          ) : topCustomersChartData.length === 0 ? (
            <div className="grid h-[280px] place-items-center text-center text-sm text-muted-foreground">
              No scanned sales yet
            </div>
          ) : (
            <HorizontalBars data={topCustomersChartData} />
          )}
        </ChartCard>
      </div>
    </>
  );
}
