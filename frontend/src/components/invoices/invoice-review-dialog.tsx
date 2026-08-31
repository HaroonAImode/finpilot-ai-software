import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  DollarSign,
  FileText,
  Loader2,
  Receipt,
  Store,
  Tag,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { InvoiceDocumentPreview } from "@/components/records/invoice-preview";
import {
  getInvoice,
  invoiceCategoryOptions,
  updateInvoice,
  validateInvoice,
  type Invoice,
  type InvoiceUpdatePayload,
  type PaymentMethod,
} from "@/lib/invoice-service";

const UNCATEGORIZED = "Uncategorized";
const PAYMENT_METHOD_OPTIONS: PaymentMethod[] = ["cash", "bank"];
const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = { cash: "Cash", bank: "Online" };

interface ReviewFormState {
  vendor_name: string;
  category: string;
  invoice_date: string;
  invoice_number: string;
  ntn: string;
  total: string;
  subtotal: string;
  tax_amount: string;
  payment_method: PaymentMethod | "__none__";
}

function formFromInvoice(inv: Invoice): ReviewFormState {
  return {
    vendor_name: inv.vendor_name ?? "",
    category: inv.category ?? "",
    invoice_date: inv.invoice_date ?? "",
    invoice_number: inv.invoice_number ?? "",
    ntn: inv.ntn ?? "",
    total: inv.total?.toString() ?? "",
    subtotal: inv.subtotal?.toString() ?? "",
    tax_amount: inv.tax_amount?.toString() ?? "",
    payment_method: inv.payment_method ?? "__none__",
  };
}

function numOrNull(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

export function InvoiceReviewDialog({
  invoiceId,
  open,
  onOpenChange,
  onSuccess,
}: {
  invoiceId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}) {
  const queryClient = useQueryClient();

  const invoiceQuery = useQuery({
    queryKey: ["invoice", invoiceId],
    queryFn: () => (invoiceId ? getInvoice(invoiceId) : Promise.reject("No id")),
    enabled: !!invoiceId && open,
  });

  const optionsQuery = useQuery({
    queryKey: ["invoice-options"],
    queryFn: invoiceCategoryOptions,
    enabled: open,
  });

  const [form, setForm] = useState<ReviewFormState | null>(null);

  const active = form ?? (invoiceQuery.data ? formFromInvoice(invoiceQuery.data) : null);

  const updateMutation = useMutation({
    mutationFn: async (payload: InvoiceUpdatePayload) => {
      if (!invoiceId) return;
      await updateInvoice(invoiceId, payload);
      await validateInvoice(invoiceId);
    },
    onSuccess: () => {
      toast.success("Invoice validated and added to Saved Records");
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
      queryClient.invalidateQueries({ queryKey: ["invoices", "needs_review"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-category-summary"] });
      queryClient.invalidateQueries({ queryKey: ["invoice-monthly-summary"] });
      if (invoiceId) queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] });
      setForm(null);
      onOpenChange(false);
      onSuccess?.();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to validate invoice");
    },
  });

  const invoice = invoiceQuery.data;

  const setField = <K extends keyof ReviewFormState>(key: K, value: ReviewFormState[K]) => {
    if (!active) return;
    setForm({ ...active, [key]: value });
  };

  const handleSubmit = () => {
    if (!active || !invoiceId) return;
    const totalNum = numOrNull(active.total);
    if (active.total.trim() !== "" && totalNum === null) {
      toast.error("Please enter a valid total amount");
      return;
    }

    const paymentMethod = active.payment_method === "__none__" ? null : active.payment_method;

    updateMutation.mutate({
      vendor_name: active.vendor_name.trim() || null,
      category: active.category.trim() || null,
      invoice_date: active.invoice_date.trim() || null,
      invoice_number: active.invoice_number.trim() || null,
      ntn: active.ntn.trim() || null,
      total: totalNum,
      subtotal: numOrNull(active.subtotal),
      tax_amount: numOrNull(active.tax_amount),
      payment_method: paymentMethod,
      status: "validated",
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-5xl overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3 pr-6">
            <div className="space-y-0.5">
              <div className="flex items-center gap-2">
                <DialogTitle className="text-lg font-bold">Review & Validate Document</DialogTitle>
                <Badge
                  variant="outline"
                  className="gap-1 border-destructive/40 bg-destructive/10 text-destructive dark:bg-destructive/20"
                >
                  <AlertTriangle className="h-3 w-3" />
                  Needs Review
                </Badge>
              </div>
              <DialogDescription className="text-xs text-muted-foreground">
                Verify AI-extracted particulars before adding to the Cash Book.
              </DialogDescription>
            </div>
            {invoice && (
              <span className="truncate rounded-md bg-muted px-2 py-1 text-xs font-mono text-muted-foreground">
                {invoice.filename}
              </span>
            )}
          </div>
        </DialogHeader>

        {invoiceQuery.isLoading || !active ? (
          <div className="grid h-[560px] grid-cols-1 gap-6 p-6 lg:grid-cols-2">
            <Skeleton className="h-full w-full rounded-xl" />
            <div className="space-y-4">
              <Skeleton className="h-10 w-3/4" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          </div>
        ) : invoiceQuery.isError || !invoice ? (
          <div className="p-8 text-center text-sm text-destructive">
            Could not load invoice details. Please try again.
          </div>
        ) : (
          <div className="grid max-h-[calc(92vh-130px)] grid-cols-1 overflow-y-auto lg:grid-cols-12">
            {/* Left: Document preview with centered 50% initial zoom */}
            <div className="border-b p-4 lg:col-span-6 lg:border-b-0 lg:border-r">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <FileText className="h-3.5 w-3.5" /> Document Preview (50% Default Zoom)
              </p>
              <div className="overflow-hidden rounded-xl">
                <InvoiceDocumentPreview
                  invoiceId={invoice.id}
                  filename={invoice.filename}
                  pageCount={invoice.page_dimensions?.length || 1}
                />
              </div>
            </div>

            {/* Right: Editable Extracted Fields */}
            <div className="flex flex-col justify-between space-y-4 p-5 lg:col-span-6">
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="flex items-center gap-1.5 text-xs font-medium">
                    <Store className="h-3.5 w-3.5 text-muted-foreground" /> Vendor / Payee
                  </Label>
                  <Input
                    value={active.vendor_name}
                    onChange={(e) => setField("vendor_name", e.target.value)}
                    placeholder="e.g. Shell Fuel Station, Metro Supermarket"
                    className="rounded-xl font-medium"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="flex items-center gap-1.5 text-xs font-medium">
                      <Tag className="h-3.5 w-3.5 text-muted-foreground" /> Category
                    </Label>
                    <Select
                      value={active.category || UNCATEGORIZED}
                      onValueChange={(v) => setField("category", v === UNCATEGORIZED ? "" : v)}
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
                    <Label className="flex items-center gap-1.5 text-xs font-medium">
                      <DollarSign className="h-3.5 w-3.5 text-muted-foreground" /> Payment Method
                    </Label>
                    <div className="flex gap-2">
                      {PAYMENT_METHOD_OPTIONS.map((method) => (
                        <button
                          key={method}
                          type="button"
                          aria-pressed={active.payment_method === method}
                          onClick={() => setField("payment_method", method)}
                          className={`flex-1 rounded-xl border px-3 py-2 text-xs font-semibold transition-colors ${
                            active.payment_method === method
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
                          }`}
                        >
                          {PAYMENT_METHOD_LABELS[method]}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="flex items-center gap-1.5 text-xs font-medium">
                      <Calendar className="h-3.5 w-3.5 text-muted-foreground" /> Invoice / Expense
                      Date
                    </Label>
                    <Input
                      type="date"
                      value={active.invoice_date}
                      onChange={(e) => setField("invoice_date", e.target.value)}
                      className="rounded-xl"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label className="flex items-center gap-1.5 text-xs font-medium">
                      <Receipt className="h-3.5 w-3.5 text-muted-foreground" /> Total Amount (PKR)
                    </Label>
                    <Input
                      value={active.total}
                      onChange={(e) => setField("total", e.target.value)}
                      placeholder="0.00"
                      className="rounded-xl font-mono font-bold"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">Invoice #</Label>
                    <Input
                      value={active.invoice_number}
                      onChange={(e) => setField("invoice_number", e.target.value)}
                      placeholder="e.g. INV-2026-001"
                      className="rounded-xl text-xs"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">Tax ID / NTN</Label>
                    <Input
                      value={active.ntn}
                      onChange={(e) => setField("ntn", e.target.value)}
                      placeholder="Optional"
                      className="rounded-xl text-xs"
                    />
                  </div>
                </div>

                <div className="rounded-xl border bg-muted/30 p-3 text-xs text-muted-foreground">
                  <p className="font-semibold text-foreground">Verification Policy</p>
                  <p className="mt-0.5">
                    Once submitted, this receipt is verified and automatically posted into your
                    Saved Records Cash Book under the chosen category and payment method.
                  </p>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-2 border-t pt-4">
                <Button
                  variant="outline"
                  className="rounded-xl"
                  onClick={() => onOpenChange(false)}
                  disabled={updateMutation.isPending}
                >
                  <X className="mr-1.5 h-4 w-4" /> Cancel
                </Button>
                <Button
                  className="gap-1.5 rounded-xl bg-primary font-semibold text-primary-foreground"
                  disabled={updateMutation.isPending}
                  onClick={handleSubmit}
                >
                  {updateMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Submitting…
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="h-4 w-4" /> Submit & Validate
                    </>
                  )}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
