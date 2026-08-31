import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronUp, FileCheck2, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { InvoiceThumbnail } from "@/components/records/invoice-thumbnail";
import { InvoiceReviewDialog } from "@/components/invoices/invoice-review-dialog";
import { listInvoices, type InvoiceListItem } from "@/lib/invoice-service";

function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return "PKR " + n.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

function particulars(row: InvoiceListItem): string {
  return row.vendor_name || row.invoice_number || row.filename || "Unverified Receipt";
}

export function NeedsReviewSection() {
  const [expanded, setExpanded] = useState(true);
  const [reviewId, setReviewId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["invoices", "needs_review"],
    queryFn: () =>
      listInvoices({
        status: ["needs_review", "needs_review_high_priority", "processed"],
        limit: 50,
      }),
    refetchInterval: 15_000,
  });

  const items = query.data?.invoices ?? [];
  const totalCount = query.data?.total ?? items.length;

  if (!query.isLoading && items.length === 0) {
    return null;
  }

  return (
    <>
      <div className="rounded-2xl border-2 border-destructive/30 bg-destructive/5 dark:bg-destructive/10 dark:border-destructive/40 p-4 sm:p-5 shadow-sm transition-all">
        {/* Header Bar */}
        <div
          className="flex cursor-pointer items-center justify-between gap-3 select-none"
          onClick={() => setExpanded((prev) => !prev)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-destructive/40 bg-destructive/15 text-destructive shadow-sm">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-display text-base font-bold tracking-tight text-foreground">
                  Documents Needing Review
                </h3>
                <Badge
                  variant="destructive"
                  className="font-mono text-xs font-semibold uppercase tracking-wider animate-pulse"
                >
                  {totalCount} Pending Verification
                </Badge>
              </div>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                Newly scanned bills & syncs awaiting human verification before joining the Cash
                Book.
              </p>
            </div>
          </div>

          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((prev) => !prev);
            }}
            aria-label={expanded ? "Collapse section" : "Expand section"}
          >
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>

        {/* Content Body */}
        {expanded && (
          <div className="mt-4 border-t border-destructive/20 pt-3">
            {query.isLoading ? (
              <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading pending reviews…
              </div>
            ) : (
              <div className="divide-y divide-destructive/15">
                {items.map((item) => (
                  <div
                    key={item.id}
                    onClick={() => setReviewId(item.id)}
                    className="group flex flex-wrap items-center justify-between gap-3 py-3 px-2 rounded-xl transition-colors hover:bg-destructive/10 cursor-pointer"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div onClick={(e) => e.stopPropagation()}>
                        <InvoiceThumbnail
                          invoiceId={item.id}
                          label={particulars(item)}
                          smallHoverPreview
                        />
                      </div>
                      <div className="min-w-0 space-y-0.5">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-bold text-foreground">
                            {particulars(item)}
                          </p>
                          <Badge
                            variant="outline"
                            className="border-destructive/40 text-[10px] bg-destructive/15 text-destructive font-medium px-1.5 py-0"
                          >
                            Unverified
                          </Badge>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          {item.invoice_date ?? "Date not specified"} ·{" "}
                          <span className="font-mono text-[11px]">{item.filename}</span>
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <div className="text-right">
                        <p className="font-mono text-sm font-bold tabular-nums text-foreground">
                          {money(item.total)}
                        </p>
                        <p className="text-[10px] text-muted-foreground">
                          {item.category || "Uncategorized"}
                        </p>
                      </div>

                      <Button
                        size="sm"
                        className="gap-1.5 rounded-xl bg-destructive hover:bg-destructive/90 text-destructive-foreground font-semibold text-xs shadow-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setReviewId(item.id);
                        }}
                      >
                        <FileCheck2 className="h-3.5 w-3.5" />
                        Review & Submit
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Review Dialog */}
      <InvoiceReviewDialog
        invoiceId={reviewId}
        open={!!reviewId}
        onOpenChange={(open) => {
          if (!open) setReviewId(null);
        }}
        onSuccess={() => {
          query.refetch();
        }}
      />
    </>
  );
}
