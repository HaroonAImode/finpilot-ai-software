import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  FileCheck2,
  Inbox,
  Loader2,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { InvoiceThumbnail } from "@/components/records/invoice-thumbnail";
import { InvoiceReviewDialog } from "@/components/invoices/invoice-review-dialog";
import { listInvoices, type InvoiceListItem } from "@/lib/invoice-service";

function formatMoney(amount: number | null): string {
  if (amount === null || amount === undefined) return "—";
  return "PKR " + amount.toLocaleString("en-PK", { maximumFractionDigits: 0 });
}

function itemTitle(item: InvoiceListItem): string {
  return item.vendor_name || item.invoice_number || item.filename || "Receipt / Bill";
}

function getSourceBadge(filename: string | null): { label: string; color: string } {
  const lower = (filename ?? "").toLowerCase();
  if (lower.startsWith("camera-capture") || lower.includes("mobile") || lower.includes("capture")) {
    return {
      label: "Mobile Camera",
      color: "border-purple-500/30 bg-purple-500/10 text-purple-500",
    };
  }
  if (lower.includes("mail") || lower.includes("email") || lower.endsWith(".eml")) {
    return { label: "Email Sync", color: "border-blue-500/30 bg-blue-500/10 text-blue-500" };
  }
  if (lower.includes("slack")) {
    return {
      label: "Slack Sync",
      color: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
    };
  }
  return { label: "Scanner Upload", color: "border-amber-500/30 bg-amber-500/10 text-amber-500" };
}

export function NeedsReviewCenter() {
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["invoices", "needs_review"],
    queryFn: () =>
      listInvoices({
        status: ["needs_review", "needs_review_high_priority", "processed"],
        limit: 25,
      }),
    refetchInterval: 15_000,
  });

  const pendingItems = query.data?.invoices ?? [];
  const pendingCount = query.data?.total ?? pendingItems.length;

  return (
    <>
      <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
        <PopoverTrigger asChild>
          <Button
            size="sm"
            className="relative gap-1.5 rounded-xl px-3 text-xs font-semibold shadow-sm transition-all"
            aria-label="Needs Review Center"
          >
            <FileCheck2 className="h-4 w-4" />
            <span className="hidden sm:inline">Needs Review</span>
            {pendingCount > 0 ? (
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-destructive px-1.5 text-[10px] font-bold text-destructive-foreground shadow-sm animate-pulse">
                {pendingCount > 99 ? "99+" : pendingCount}
              </span>
            ) : (
              <span className="h-2 w-2 rounded-full bg-emerald-300 dark:bg-emerald-400" />
            )}
          </Button>
        </PopoverTrigger>

        <PopoverContent align="end" className="w-[380px] p-0 shadow-2xl sm:w-[440px]">
          {/* Header */}
          <div className="flex items-center justify-between border-b px-4 py-3.5 bg-muted/40">
            <div className="flex items-center gap-2">
              <div className="grid h-8 w-8 place-items-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-500">
                <AlertTriangle className="h-4 w-4" />
              </div>
              <div>
                <p className="font-display text-sm font-bold leading-tight">Needs Review Center</p>
                <p className="text-[11px] text-muted-foreground">
                  New receipts & bills awaiting your confirmation
                </p>
              </div>
            </div>
            {pendingCount > 0 && (
              <Badge variant="destructive" className="font-mono text-[10px]">
                {pendingCount} Pending
              </Badge>
            )}
          </div>

          {/* Body */}
          <div className="max-h-[380px] overflow-y-auto">
            {query.isLoading ? (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
                <p className="text-xs">Checking for pending documents…</p>
              </div>
            ) : pendingItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-center px-4">
                <div className="grid h-10 w-10 place-items-center rounded-full bg-success/15 text-success">
                  <CheckCircle className="h-5 w-5" />
                </div>
                <p className="text-sm font-semibold">All caught up!</p>
                <p className="text-xs text-muted-foreground">
                  No new receipts or invoices requiring verification.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {pendingItems.map((item) => {
                  const source = getSourceBadge(item.filename);
                  return (
                    <li
                      key={item.id}
                      className="group flex items-start gap-3 p-3.5 transition-colors hover:bg-muted/40"
                    >
                      <InvoiceThumbnail
                        invoiceId={item.id}
                        label={itemTitle(item)}
                        smallHoverPreview
                      />
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-xs font-bold text-foreground">
                            {itemTitle(item)}
                          </p>
                          <span className="shrink-0 font-mono text-xs font-bold text-primary">
                            {formatMoney(item.total)}
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                          <span>{item.invoice_date ?? "Recent"}</span>
                          <span>·</span>
                          <span className="truncate max-w-[120px]">{item.filename}</span>
                        </div>
                        <div className="flex items-center justify-between gap-2 pt-1">
                          <Badge
                            variant="outline"
                            className={`text-[9px] px-1.5 py-0 font-medium ${source.color}`}
                          >
                            {source.label}
                          </Badge>
                          <Button
                            size="sm"
                            className="h-6 rounded-lg px-2.5 text-[11px] font-semibold"
                            onClick={() => {
                              setReviewId(item.id);
                              setPopoverOpen(false);
                            }}
                          >
                            Review Now
                          </Button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Footer */}
          <div className="border-t bg-muted/20 px-4 py-2.5 text-center">
            <Link
              to="/app/records"
              onClick={() => setPopoverOpen(false)}
              className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
            >
              Go to Saved Records Cash Book <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
        </PopoverContent>
      </Popover>

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
