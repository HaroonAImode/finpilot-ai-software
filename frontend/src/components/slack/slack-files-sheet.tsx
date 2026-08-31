import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, ChevronLeft, ChevronRight, Eye, FileText, Inbox, RefreshCw, Search, Send,
} from "lucide-react";
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  listSlackFiles, retrySlackFile, sendSlackFileToScanner, updateSlackFileCategory,
  type SlackFile,
} from "@/lib/slack-connector";
import { toast } from "sonner";
import { DocumentPreviewDialog } from "@/components/documents/document-preview-dialog";
import { SCANNER_ELIGIBLE as SCANNER_CATEGORIES, previewKindFor, type UnifiedDocument } from "@/lib/documents";

/** Reuse the shared preview dialog by mapping a Slack file into the unified shape. */
function toUnifiedDocument(file: SlackFile): UnifiedDocument {
  return {
    id: file.id,
    source: "slack",
    filename: file.filename,
    title: file.title,
    fileType: file.file_type,
    mimetype: file.mimetype,
    size: file.size,
    createdAt: file.created_at,
    category: file.category,
    categorySource: file.category_source,
    downloaded: file.downloaded,
    downloadFailed: file.download_failed,
    sharedBy: file.shared_by_user_name,
    externalUrl: file.slack_permalink || null,
    contentUrl: file.downloaded ? `/api/v1/slack/files/${file.id}/content` : null,
    scannerEligible: SCANNER_CATEGORIES.has(file.category),
    conversationId: file.conversation_id,
  };
}

const CATEGORIES = [
  "Invoices", "Receipts", "Contracts", "Project Plans", "Reports", "Presentations",
  "Spreadsheets & Financial", "Images & Screenshots", "Bookings & Reservations",
  "Archives", "Audio & Video", "uncategorized",
];

/** Categories the AI Scanner can extract structured invoice data from. */
const SCANNER_ELIGIBLE = new Set(["Invoices", "Receipts", "Spreadsheets & Financial"]);

const PAGE_SIZE = 20;

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const unit = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, unit)).toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

export function SlackFilesSheet({
  open, onOpenChange, scanTarget = "purchase",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** "sale" sends a file to the Revenue Manager's scan-to-revenue endpoint
   *  instead of the purchase Scanner's (design spec docs/superpowers/
   *  specs/2026-08-27-revenue-manager-scan-design.md §8.4). The Scanner
   *  itself passes nothing, so its behavior is unchanged. */
  scanTarget?: "purchase" | "sale";
}) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(0);
  /** Tracks which single file each row-level action is running for, so one row's
   *  spinner never disables every other row's button. */
  const [scanningId, setScanningId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [previewFile, setPreviewFile] = useState<SlackFile | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);

  // A filter change makes the current offset meaningless — go back to the first page.
  useEffect(() => {
    setPage(0);
  }, [category]);

  const filesQuery = useQuery({
    queryKey: ["slack-files", category, page],
    queryFn: () =>
      listSlackFiles(
        category
          ? { category, skip: page * PAGE_SIZE, limit: PAGE_SIZE }
          : { skip: page * PAGE_SIZE, limit: PAGE_SIZE },
      ),
    enabled: open,
  });

  const categoryMutation = useMutation({
    mutationFn: ({ fileId, newCategory }: { fileId: string; newCategory: string }) =>
      updateSlackFileCategory(fileId, newCategory),
    onSuccess: () => {
      toast.success("Category updated");
      queryClient.invalidateQueries({ queryKey: ["slack-files"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not update the category"),
  });

  const sendToScannerMutation = useMutation({
    mutationFn: (fileId: string) => sendSlackFileToScanner(fileId, scanTarget),
    onSuccess: () =>
      toast.success(
        scanTarget === "sale"
          ? "Sent to Revenue Manager — check that page for extraction progress"
          : "Sent to AI Scanner — check the Scanner page for extraction progress",
      ),
    onError: (error: Error) =>
      toast.error(error.message || `Could not send this file to the ${scanTarget === "sale" ? "Revenue Manager" : "scanner"}`),
    onSettled: () => setScanningId(null),
  });

  const retryMutation = useMutation({
    mutationFn: retrySlackFile,
    onSuccess: () => {
      toast.success("Re-queued — the worker will retry this download");
      queryClient.invalidateQueries({ queryKey: ["slack-files"] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not retry this download"),
    onSettled: () => setRetryingId(null),
  });

  const total = filesQuery.data?.total ?? 0;
  const pageFiles = filesQuery.data?.files ?? [];
  // Search narrows the current page only — the API has no search parameter yet,
  // so the placeholder says "on this page" rather than implying a global search.
  const visibleFiles = pageFiles.filter((file: SlackFile) =>
    `${file.filename} ${file.title ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );

  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);
  const hasFilter = Boolean(category) || search.length > 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Synced Slack files</SheetTitle>
          <SheetDescription>
            Files discovered in your Slack conversations. Correct a category if the classifier got
            it wrong, then send invoices and receipts to the AI Scanner.
          </SheetDescription>
        </SheetHeader>

        <div className="flex gap-2 px-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search on this page"
              className="rounded-xl pl-9"
            />
          </div>
          <Select
            value={category ?? "all"}
            onValueChange={(value) => setCategory(value === "all" ? undefined : value)}
          >
            <SelectTrigger className="w-48 rounded-xl"><SelectValue placeholder="All categories" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        <div className="mt-4 flex-1 space-y-3 overflow-y-auto px-4 pb-4">
          {/* isPending, not isLoading: between retry attempts isLoading flips false while
              data is still undefined, which would fall through to the "no files" state and
              claim nothing is synced when we simply have not loaded yet. */}
          {filesQuery.isPending && (
            <div className="space-y-3" aria-busy="true" aria-label="Loading files">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="surface flex items-start gap-3 p-4">
                  <Skeleton className="h-5 w-5 shrink-0 rounded" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-2/5" />
                    <Skeleton className="h-3 w-3/5" />
                    <Skeleton className="h-7 w-32 rounded-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {filesQuery.isError && (
            <div className="surface flex flex-col items-center gap-3 p-8 text-center">
              <AlertCircle className="h-8 w-8 text-destructive" />
              <div>
                <p className="text-sm font-medium">Could not load your Slack files</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {(filesQuery.error as Error)?.message ?? "The connector service did not respond."}
                </p>
              </div>
              <Button variant="outline" size="sm" className="gap-1.5 rounded-xl" onClick={() => filesQuery.refetch()}>
                <RefreshCw className="h-3.5 w-3.5" /> Try again
              </Button>
            </div>
          )}

          {/* Nothing synced at all reads very differently from "your filter excluded
              everything" — the first needs a sync, the second needs a filter reset.
              Both are gated on isSuccess so a failed request never renders as "no files". */}
          {filesQuery.isSuccess && total === 0 && !hasFilter && (
            <div className="surface flex flex-col items-center gap-2 p-8 text-center">
              <Inbox className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">No files synced yet</p>
              <p className="text-xs text-muted-foreground">
                Run <span className="font-medium">Sync now</span> to discover files shared in your Slack conversations.
              </p>
            </div>
          )}

          {filesQuery.isSuccess && visibleFiles.length === 0 && (total > 0 || hasFilter) && (
            <div className="surface flex flex-col items-center gap-2 p-8 text-center">
              <Search className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">No files match these filters</p>
              <Button
                variant="ghost"
                size="sm"
                className="rounded-xl"
                onClick={() => { setSearch(""); setCategory(undefined); }}
              >
                Clear filters
              </Button>
            </div>
          )}

          {visibleFiles.map((file) => (
            <div key={file.id} className="surface flex items-start justify-between gap-3 p-4">
              <div className="flex min-w-0 items-start gap-3">
                <FileText className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium" title={file.filename}>{file.filename}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)} · {new Date(file.created_at).toLocaleDateString()}
                    {file.shared_by_user_name ? ` · shared by ${file.shared_by_user_name}` : ""}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Select
                      value={file.category}
                      onValueChange={(value) => categoryMutation.mutate({ fileId: file.id, newCategory: value })}
                    >
                      <SelectTrigger
                        className="h-7 w-auto rounded-full border-none bg-muted/60 px-3 text-xs"
                        aria-label={`Category for ${file.filename}`}
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                      </SelectContent>
                    </Select>
                    {file.category_source === "manual_override" && (
                      <span className="text-[11px] text-muted-foreground">edited</span>
                    )}
                    {file.downloaded ? (
                      <Badge variant="outline" className="text-success">Ready</Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className={file.download_failed ? "text-destructive" : "text-muted-foreground"}
                      >
                        {file.download_failed ? "Failed" : "Pending"}
                      </Badge>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 rounded-lg"
                  onClick={() => { setPreviewFile(file); setPreviewOpen(true); }}
                  disabled={!file.downloaded}
                  title={
                    file.downloaded
                      ? previewKindFor(file.mimetype, file.filename) === "unsupported"
                        ? "Opens a download for this file type"
                        : "Preview without leaving FinPilot"
                      : "Not downloaded yet"
                  }
                >
                  <Eye className="h-3.5 w-3.5" /> Preview
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <a href={file.slack_permalink} target="_blank" rel="noreferrer">View in Slack</a>
                </Button>
                {file.download_failed && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 rounded-lg"
                    onClick={() => { setRetryingId(file.id); retryMutation.mutate(file.id); }}
                    disabled={retryingId === file.id}
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${retryingId === file.id ? "animate-spin" : ""}`} />
                    {retryingId === file.id ? "Retrying…" : "Retry download"}
                  </Button>
                )}
                {SCANNER_ELIGIBLE.has(file.category) && file.downloaded && (
                  <Button
                    size="sm"
                    className="gap-1.5 rounded-lg"
                    onClick={() => { setScanningId(file.id); sendToScannerMutation.mutate(file.id); }}
                    disabled={scanningId === file.id}
                  >
                    <Send className="h-3.5 w-3.5" />
                    {scanningId === file.id ? "Sending…" : scanTarget === "sale" ? "Send to Revenue" : "Send to Scanner"}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>

        {total > 0 && !filesQuery.isError && (
          <div className="flex items-center justify-between gap-2 border-t px-4 py-3">
            <p className="text-xs text-muted-foreground">
              {rangeStart}–{rangeEnd} of {total}
              {search.length > 0 && visibleFiles.length !== pageFiles.length
                ? ` · ${visibleFiles.length} shown on this page`
                : ""}
            </p>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="sm"
                className="gap-1 rounded-xl"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0 || filesQuery.isFetching}
              >
                <ChevronLeft className="h-3.5 w-3.5" /> Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="gap-1 rounded-xl"
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                disabled={page >= lastPage || filesQuery.isFetching}
              >
                Next <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </SheetContent>

      <DocumentPreviewDialog
        document={previewFile ? toUnifiedDocument(previewFile) : null}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
      />
    </Sheet>
  );
}
