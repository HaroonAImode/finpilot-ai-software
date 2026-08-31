import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FileQuestion, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { InvoiceThumbnail } from "@/components/records/invoice-thumbnail";
import {
  invoiceCategoryOptions, listInvoices, reclassifyInvoice, rejectInvoice, updateInvoice,
  validateInvoice, type InvoiceListItem,
} from "@/lib/invoice-service";

const UNCATEGORIZED = "Uncategorized";

/** How each detected type reads to a person. Deliberately plain language —
 *  "Approval / Minute Sheet", not "minute_sheet" — since the whole point of
 *  this area is explaining what the system decided, not exposing its
 *  vocabulary. */
const TYPE_LABELS: Record<string, string> = {
  minute_sheet: "Approval / Minute Sheet",
  approval_request: "Approval Request",
};

/** The stored extraction is the source of truth for what the rules engine
 *  decided — it is never overwritten by a human correction (Rule 8.1), so
 *  this keeps showing the original detection even after an override. */
function typeLabel(row: InvoiceListItem): string {
  const detected = row.detected_document_type;
  return (detected && TYPE_LABELS[detected]) || "Non-Transactional Document";
}

function formatAmount(amount: number | null): string {
  if (amount === null) return "—";
  return `Rs. ${amount.toLocaleString("en-PK", { maximumFractionDigits: 2 })}`;
}

/**
 * The dedicated area for documents that carry amounts but are not financial
 * transactions — an internal minute sheet, an approval request.
 *
 * Deliberately *not* an "Uncategorized" bucket in the cashbook: that would
 * throw away what the document actually is and read as a categorization
 * failure, when in fact the system correctly identified a document that
 * does not belong in the cashbook at all. Everything extracted is still
 * shown here, including the amount — as "Amount mentioned", never as a
 * transaction total.
 */
export function NonTransactionalDocuments() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["invoices", "non-transactional"],
    queryFn: () => listInvoices({ transactionStatus: "non_transactional", status: "all", limit: 200 }),
  });
  // Same cache entry Saved Records' own per-row "Change Category" menu uses
  // (["invoice-options"]) — free if that page already fetched it, and never
  // a second network round-trip just for this list.
  const optionsQuery = useQuery({ queryKey: ["invoice-options"], queryFn: invoiceCategoryOptions });
  const categoryOptions = optionsQuery.data?.categories ?? [];

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["invoices", "non-transactional"] });
    // The cashbook's own lists can change too — promoting a document to a
    // transaction adds a row there, which moves its amount into the Cash
    // Book's own monthly totals (same reason RecordDetail's edit save
    // invalidates this) — without it, the top summary strip keeps showing
    // the pre-promotion total while the category list/chart below it (a
    // different query) already reflects the new one.
    queryClient.invalidateQueries({ queryKey: ["invoices", "records"] });
    queryClient.invalidateQueries({ queryKey: ["invoice-category-summary"] });
    queryClient.invalidateQueries({ queryKey: ["invoice-monthly-summary"] });
  }

  const confirmMutation = useMutation({
    mutationFn: (id: string) => validateInvoice(id),
    onSuccess: () => {
      toast.success("Confirmed as a non-transactional document");
      refresh();
    },
    onError: (error: Error) => toast.error(error.message || "Could not confirm this document"),
  });

  // "Process as Transaction" always asks which category up front rather
  // than landing the record in Uncategorized and making a person fix it
  // as a second step — reclassify() alone never assigns one (see its own
  // docstring), so this picker is the only place that decision gets made.
  const promoteMutation = useMutation({
    mutationFn: async ({ id, category }: { id: string; category: string | null }) => {
      await reclassifyInvoice(id, "transactional");
      return updateInvoice(id, { category });
    },
    onSuccess: (updated) => {
      toast.success(`Moved into transaction processing — ${updated.category ?? UNCATEGORIZED}`);
      refresh();
    },
    onError: (error: Error) => toast.error(error.message || "Could not reclassify this document"),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => rejectInvoice(id),
    onSuccess: () => {
      toast.success("Rejected — the document is kept and this can be undone");
      refresh();
    },
    onError: (error: Error) => toast.error(error.message || "Could not reject this document"),
  });

  const busyId =
    confirmMutation.isPending ? confirmMutation.variables
      : promoteMutation.isPending ? promoteMutation.variables?.id
        : rejectMutation.isPending ? rejectMutation.variables
          : null;

  if (query.isPending) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (query.isError) {
    return (
      <p className="text-sm text-destructive">
        Could not load non-transactional documents. {(query.error as Error).message}
      </p>
    );
  }

  const rows = query.data?.invoices ?? [];
  if (rows.length === 0) return null;

  return (
    <section className="space-y-3" aria-labelledby="non-transactional-heading">
      <div className="flex items-center gap-2">
        <FileQuestion className="size-4 text-muted-foreground" aria-hidden />
        <h2 id="non-transactional-heading" className="text-sm font-semibold">
          Non-Transactional Documents
        </h2>
        <Badge variant="secondary">{rows.length}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        These carry amounts but are not purchases, so they are kept out of the cashbook and its totals.
        Nothing extracted from them has been discarded.
      </p>

      <ul className="space-y-2">
        {rows.map((row) => {
          const busy = busyId === row.id;
          return (
            <li key={row.id} className="rounded-lg border bg-card p-3">
              <div className="flex gap-3">
                <InvoiceThumbnail invoiceId={row.id} label={row.filename ?? typeLabel(row)} smallHoverPreview />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{typeLabel(row)}</span>
                    {row.classification_source === "user_override" ? (
                      <Badge variant="outline" className="text-[10px]">Corrected by you</Badge>
                    ) : null}
                    {row.status === "rejected" ? (
                      <Badge variant="destructive" className="text-[10px]">Rejected</Badge>
                    ) : null}
                    {row.status === "validated" ? (
                      <Badge variant="secondary" className="text-[10px]">Confirmed</Badge>
                    ) : null}
                  </div>

                  <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                    <div className="flex gap-1.5">
                      <dt className="text-muted-foreground">Amount mentioned</dt>
                      <dd className="font-medium tabular-nums">{formatAmount(row.amount_mentioned)}</dd>
                    </div>
                    <div className="flex gap-1.5">
                      <dt className="text-muted-foreground">Confidence</dt>
                      <dd className="tabular-nums">{Math.round(row.document_confidence * 100)}%</dd>
                    </div>
                    {row.classification_reason ? (
                      <div className="col-span-full space-y-0.5">
                        <dt className="text-muted-foreground">Reason</dt>
                        <dd className="italic text-muted-foreground">
                          Detected wording: &ldquo;{row.classification_reason}&rdquo;
                        </dd>
                      </div>
                    ) : null}
                    {row.filename ? (
                      <div className="col-span-full flex gap-1.5 truncate">
                        <dt className="text-muted-foreground">Document</dt>
                        <dd className="truncate">{row.filename}</dd>
                      </div>
                    ) : null}
                  </dl>

                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      size="sm" variant="secondary" disabled={busy || row.status === "validated"}
                      onClick={() => confirmMutation.mutate(row.id)}
                    >
                      {busy && confirmMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                      Confirm
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size="sm" variant="outline" disabled={busy}>
                          {busy && promoteMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                          Process as Transaction
                          <ChevronDown className="size-3" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
                        <DropdownMenuLabel>Move to category</DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onSelect={() => promoteMutation.mutate({ id: row.id, category: null })}>
                          {UNCATEGORIZED}
                        </DropdownMenuItem>
                        {categoryOptions.map((c) => (
                          <DropdownMenuItem key={c} onSelect={() => promoteMutation.mutate({ id: row.id, category: c })}>
                            {c}
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                      size="sm" variant="ghost" disabled={busy || row.status === "rejected"}
                      onClick={() => rejectMutation.mutate(row.id)}
                    >
                      {busy && rejectMutation.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                      Reject
                    </Button>
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
