import { useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, CheckCircle2, Inbox, Plug, RefreshCw, Sparkles, Trash2, Upload,
} from "lucide-react";
import { Link } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { DocumentRow } from "@/components/documents/document-row";
import { DocumentTile } from "@/components/documents/document-tile";
import { VirtualizedFileBrowser } from "@/components/documents/virtualized-file-browser";
import { ConversationGroup } from "@/components/documents/conversation-group";
import { DeleteDocumentDialog } from "@/components/documents/delete-document-dialog";
import { SyncPickerDialog } from "@/components/slack/sync-picker-dialog";
import {
  type DocumentSource, type DocumentView, type FileBrowserViewMode, type UnifiedDocument,
} from "@/lib/documents";

const PAGE_SIZE = 60;

/** Icon views use a fixed column count rather than a fully fluid reflow —
 *  this column now fills the page's whole width (one source browsed at a
 *  time, not four side by side), so a flat number per view mode is a
 *  reasonable trade-off against wiring a ResizeObserver for what is, in
 *  practice, one of three fixed layouts a person picks from a toggle. */
const COLUMNS_FOR_VIEW: Record<FileBrowserViewMode, number> = {
  list: 1,
  // Small icons is a compact icon+filename row (DocumentTile's "sm" layout),
  // not a shrunken square tile — fewer, wider columns so the filename next
  // to each icon actually has room to read.
  "small-icons": 4,
  "large-icons": 5,
};

const ROW_HEIGHT_FOR_VIEW: Record<FileBrowserViewMode, number> = {
  // DocumentRow is now a single compact line (icon, filename, badges,
  // actions), not a three-line card — matches its actual rendered height.
  list: 56,
  "small-icons": 52,
  "large-icons": 156,
};

/**
 * One source's file browser — everything from a single connector, browsed
 * one source at a time from the Documents landing page's folder grid
 * (app.documents.tsx), not four columns visible side by side at once.
 *
 * Sources still to be built render a "planned" column rather than being hidden,
 * so the page shows the full intended shape of the product without inventing
 * documents that do not exist.
 *
 * The flat view is paginated (useInfiniteQuery, PAGE_SIZE at a time) and
 * rendered through VirtualizedFileBrowser, so browsing a source with tens
 * of thousands of files costs the same DOM size — and the same first-paint
 * time — as one with a handful (see that component's own docstring).
 * Grouped-by-conversation mode is deliberately out of that scope for now:
 * each conversation already shards its own files into a much smaller,
 * independently-lazy-loaded chunk (ConversationGroup), so the "20,000 in
 * one view" problem this pass exists to solve does not really occur there
 * — it keeps its original plain-row rendering.
 */
export function SourceColumn({
  source,
  category,
  search,
  view,
  viewMode,
  onPreview,
  selectedIds,
  onToggleSelect,
  onSelectMany,
}: {
  source: DocumentSource;
  category: string | undefined;
  search: string;
  view: DocumentView;
  /** List / Small Icons / Large Icons — only meaningful in flat mode; a
   *  grouped-by-conversation column still renders each conversation's
   *  files as plain rows (see the module docstring's scope note). */
  viewMode: FileBrowserViewMode;
  onPreview: (document: UnifiedDocument) => void;
  selectedIds: Set<string>;
  onToggleSelect: (document: UnifiedDocument) => void;
  onSelectMany: (documents: UnifiedDocument[], selected: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [scanningId, setScanningId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // The document awaiting confirmation, not the one currently being deleted —
  // clicking Trash opens the dialog; the mutation only fires once the user
  // actually confirms in it.
  const [deleteTarget, setDeleteTarget] = useState<UnifiedDocument | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const Icon = source.icon;

  // Grouping only applies to a source that both wants it (view) and can
  // supply it (fetchConversations) — Email/WhatsApp stay flat even in grouped
  // mode until they gain a conversations concept.
  const grouped = view === "grouped" && Boolean(source.fetchConversations);

  const connectionQuery = useQuery({
    queryKey: ["source-connection", source.id],
    queryFn: () => source.getConnection!(),
    enabled: source.status === "available" && Boolean(source.getConnection),
  });

  // A source without getConnection has nothing to connect — the browser-upload
  // source is "connected" by definition, since its documents are here because
  // someone uploaded them. Treating a missing getConnection as disconnected
  // would leave that column permanently showing "Not connected".
  const connected = source.getConnection ? (connectionQuery.data?.connected ?? false) : true;

  // Paginated, not "fetch everything" — see the module docstring. Each page
  // asks for PAGE_SIZE more starting where the last one left off; the next
  // page is only requested once VirtualizedFileBrowser reports the user has
  // actually scrolled near the end of what is already loaded.
  const documentsQuery = useInfiniteQuery({
    queryKey: ["source-documents", source.id, category],
    queryFn: ({ pageParam }) =>
      source.fetchDocuments!({ skip: pageParam, limit: PAGE_SIZE, ...(category ? { category } : {}) }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, page) => sum + page.documents.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    // Skipped entirely in grouped mode: each conversation's files load lazily
    // inside its own ConversationGroup instead of one flat fetch here.
    enabled: source.status === "available" && Boolean(source.fetchDocuments) && connected && !grouped,
  });

  const conversationsQuery = useQuery({
    queryKey: ["source-conversations", source.id],
    queryFn: () => source.fetchConversations!(),
    enabled: grouped && connected,
  });

  const scannerMutation = useMutation({
    mutationFn: (documentId: string) => source.sendToScanner!(documentId),
    onSuccess: () => toast.success("Sent to AI Scanner — check the Scanner page for progress"),
    onError: (error: Error) => toast.error(error.message || "Could not send this document to the scanner"),
    onSettled: () => setScanningId(null),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => source.uploadDocument!(file),
    onSuccess: () => {
      toast.success("Uploaded — it will stay here until you delete it");
      queryClient.invalidateQueries({ queryKey: ["source-documents", source.id] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not upload this file"),
  });

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => source.deleteDocument!(documentId),
    onSuccess: () => {
      toast.success("Document removed");
      queryClient.invalidateQueries({ queryKey: ["source-documents", source.id] });
      setDeleteTarget(null);
    },
    onError: (error: Error) => toast.error(error.message || "Could not remove this document"),
    onSettled: () => setDeletingId(null),
  });

  const retryMutation = useMutation({
    mutationFn: (documentId: string) => source.retryDownload!(documentId),
    onSuccess: () => {
      toast.success("Re-queued — the worker will retry this download");
      queryClient.invalidateQueries({ queryKey: ["source-documents", source.id] });
    },
    onError: (error: Error) => toast.error(error.message || "Could not retry this download"),
    onSettled: () => setRetryingId(null),
  });

  // Conversations needing attention surface first, then busiest first — the
  // same ordering a good ops tool uses: show what's broken, then what matters
  // most among the rest.
  const sortedConversations = [...(conversationsQuery.data?.conversations ?? [])].sort((a, b) => {
    if (Boolean(a.errorHint) !== Boolean(b.errorHint)) return a.errorHint ? -1 : 1;
    return b.fileCount - a.fileCount;
  });

  // Every page loaded so far, flattened — not "all 30,000 rows": only as
  // many pages exist as the user has actually scrolled through (or as many
  // as VirtualizedFileBrowser's end-reached callback has requested).
  const allDocuments = documentsQuery.data?.pages.flatMap((page) => page.documents) ?? [];
  const documents = allDocuments.filter((doc) =>
    `${doc.filename} ${doc.title ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );
  // The server's real total, from the first page — accurate regardless of
  // how many pages have actually loaded, unlike counting `allDocuments`.
  const total = grouped
    ? sortedConversations.reduce((sum, c) => sum + c.fileCount, 0)
    : documentsQuery.data?.pages[0]?.total ?? 0;
  const allSelected = documents.length > 0 && documents.every((doc) => selectedIds.has(doc.id));
  const columns = COLUMNS_FOR_VIEW[viewMode];

  return (
    <section className="surface flex h-full min-h-[24rem] flex-col overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-muted">
            <Icon className="h-4.5 w-4.5" />
          </span>
          <div>
            <h2 className="text-sm font-semibold">{source.label}</h2>
            {source.status === "planned" ? (
              <p className="text-xs text-muted-foreground">Not built yet</p>
            ) : !source.getConnection ? (
              <p className="text-xs text-muted-foreground">Uploaded from this browser</p>
            ) : connectionQuery.isPending ? (
              <Skeleton className="mt-1 h-3 w-20" />
            ) : connected ? (
              <p className="truncate text-xs text-muted-foreground" title={connectionQuery.data?.accountLabel ?? ""}>
                {connectionQuery.data?.accountLabel ?? "Connected"}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">Not connected</p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {source.status === "available" && source.uploadDocument && (
            <>
              <input
                ref={uploadInputRef}
                type="file"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  // Reset first: picking the same file twice in a row fires no
                  // change event otherwise, so a retry after an error would
                  // silently do nothing.
                  e.target.value = "";
                  if (file) uploadMutation.mutate(file);
                }}
              />
              <Button
                size="sm" variant="outline" className="gap-1.5 rounded-xl"
                disabled={uploadMutation.isPending}
                onClick={() => uploadInputRef.current?.click()}
              >
                <Upload className="h-3.5 w-3.5" />
                {uploadMutation.isPending ? "Uploading…" : "Upload"}
              </Button>
            </>
          )}
          {source.status === "available" && connected && source.startSync && (
            <SyncPickerDialog
              source={source}
              onSyncStarted={() => {
                toast("Sync started — files will appear here as they're found.");
                queryClient.invalidateQueries({ queryKey: ["source-conversations", source.id] });
                queryClient.invalidateQueries({ queryKey: ["source-documents", source.id] });
              }}
              trigger={
                <Button size="sm" variant="outline" className="gap-1.5 rounded-xl">
                  <RefreshCw className="h-3.5 w-3.5" /> Sync
                </Button>
              }
            />
          )}
          {source.status === "available" && connected && (
            <Badge variant="outline" className="gap-1 text-success">
              <CheckCircle2 className="h-3 w-3" />
              {grouped || !search ? total : `${documents.length}/${total}`}
            </Badge>
          )}
          {grouped && (conversationsQuery.data?.needsAttention ?? 0) > 0 && (
            <Badge variant="outline" className="gap-1 text-destructive">
              {conversationsQuery.data!.needsAttention} need attention
            </Badge>
          )}
          {/* Select-all is per-group in the grouped view — a page-wide toggle
              would silently include documents inside collapsed sections the
              user has not looked at. */}
          {!grouped && documents.length > 0 && (
            <label
              className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground"
              title={`Select all loaded ${source.label} documents`}
            >
              <Checkbox
                checked={allSelected}
                onCheckedChange={(checked) => onSelectMany(documents, checked === true)}
                aria-label={`Select all ${source.label} documents`}
              />
              All
            </label>
          )}
        </div>
      </header>

      {/* overflow-hidden, not overflow-y-auto: VirtualizedFileBrowser (flat
          mode) and the grouped conversation list each own their own scroll
          container below — nesting two scrollers here would mean two
          scrollbars fighting over the same content. */}
      <div className="flex flex-1 flex-col overflow-hidden p-3">
        {source.status === "planned" && (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <Sparkles className="h-7 w-7 text-muted-foreground" />
            <p className="text-sm font-medium">{source.label} connector coming next</p>
            <p className="max-w-[15rem] text-xs text-muted-foreground">{source.plannedDescription}</p>
          </div>
        )}

        {source.status === "available" && !connectionQuery.isPending && !connected && (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
            <Plug className="h-7 w-7 text-muted-foreground" />
            <p className="text-sm font-medium">{source.label} is not connected</p>
            <p className="max-w-[15rem] text-xs text-muted-foreground">
              Connect it to start pulling documents into FinPilot.
            </p>
            <Button asChild size="sm" variant="outline" className="rounded-xl">
              <Link to="/app/settings">Go to Settings</Link>
            </Button>
          </div>
        )}

        {/* A source can be connected but not yet able to list documents (a
            connector built up to OAuth and no further). Kept as an honest
            state rather than a permanently-spinning skeleton, since an
            `enabled: false` query never resolves isPending to false. */}
        {source.status === "available" && connected && !source.fetchDocuments && (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <Sparkles className="h-7 w-7 text-muted-foreground" />
            <p className="text-sm font-medium">{source.label} is connected</p>
            <p className="max-w-[15rem] text-xs text-muted-foreground">
              Pulling documents in isn't built yet — this connects the account for now.
            </p>
          </div>
        )}

        {connectionQuery.isError && (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <AlertCircle className="h-7 w-7 text-destructive" />
            <p className="text-sm font-medium">{source.label} service unreachable</p>
            <p className="max-w-[15rem] text-xs text-muted-foreground">
              {(connectionQuery.error as Error)?.message ?? "No response from the connector."}
            </p>
            <Button size="sm" variant="outline" className="gap-1.5 rounded-xl" onClick={() => connectionQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" /> Retry
            </Button>
          </div>
        )}

        {grouped && (
          <div className="h-full space-y-2 overflow-y-auto">
            {connected && conversationsQuery.isPending && (
              <div className="space-y-2" aria-busy="true" aria-label={`Loading ${source.label} conversations`}>
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-xl" />
                ))}
              </div>
            )}

            {connected && conversationsQuery.isError && (
              <div className="flex flex-col items-center gap-2 p-6 text-center">
                <AlertCircle className="h-7 w-7 text-destructive" />
                <p className="text-sm font-medium">Could not load conversations</p>
                <Button
                  size="sm" variant="outline" className="gap-1.5 rounded-xl"
                  onClick={() => conversationsQuery.refetch()}
                >
                  <RefreshCw className="h-3.5 w-3.5" /> Try again
                </Button>
              </div>
            )}

            {connected && !conversationsQuery.isPending && !conversationsQuery.isError
              && sortedConversations.length === 0 && (
              <div className="flex flex-col items-center gap-2 p-6 text-center">
                <Inbox className="h-7 w-7 text-muted-foreground" />
                <p className="text-sm font-medium">No conversations synced yet</p>
                <p className="max-w-[15rem] text-xs text-muted-foreground">
                  Run a sync from Settings to discover channels and DMs.
                </p>
              </div>
            )}

            {sortedConversations.map((conv, index) => (
              <ConversationGroup
                key={conv.id}
                source={source}
                conversation={conv}
                category={category}
                search={search}
                onPreview={onPreview}
                selectedIds={selectedIds}
                onToggleSelect={onToggleSelect}
                onSelectMany={onSelectMany}
                canDelete={Boolean(source.deleteDocument)}
                onDeleteRequest={setDeleteTarget}
                // The channel most likely to matter — top of the needs-attention-
                // first, busiest-first sort — opens by default so the column is
                // not just a wall of collapsed headers on first load.
                defaultOpen={index === 0}
              />
            ))}
          </div>
        )}

        {!grouped && connected && Boolean(source.fetchDocuments) && documentsQuery.isPending && (
          <div className="space-y-2" aria-busy="true" aria-label={`Loading ${source.label} documents`}>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="rounded-xl border p-3">
                <Skeleton className="h-4 w-3/5" />
                <Skeleton className="mt-2 h-3 w-2/5" />
              </div>
            ))}
          </div>
        )}

        {!grouped && connected && Boolean(source.fetchDocuments) && documentsQuery.isError && (
          <div className="flex flex-col items-center gap-2 p-6 text-center">
            <AlertCircle className="h-7 w-7 text-destructive" />
            <p className="text-sm font-medium">Could not load documents</p>
            <Button size="sm" variant="outline" className="gap-1.5 rounded-xl" onClick={() => documentsQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" /> Try again
            </Button>
          </div>
        )}

        {!grouped && connected && Boolean(source.fetchDocuments) && !documentsQuery.isPending && !documentsQuery.isError && documents.length === 0 && (
          <div className="flex flex-col items-center gap-2 p-6 text-center">
            <Inbox className="h-7 w-7 text-muted-foreground" />
            <p className="text-sm font-medium">
              {total === 0 ? "No documents yet" : "Nothing matches these filters"}
            </p>
            {total === 0 && (
              <p className="max-w-[15rem] text-xs text-muted-foreground">
                Run a sync from Settings to pull documents in.
              </p>
            )}
          </div>
        )}

        {!grouped && connected && Boolean(source.fetchDocuments) && documents.length > 0 && (
          <VirtualizedFileBrowser
            className="h-full"
            items={documents}
            getId={(doc) => doc.id}
            columns={columns}
            rowHeight={ROW_HEIGHT_FOR_VIEW[viewMode]}
            hasMore={documentsQuery.hasNextPage}
            isFetchingMore={documentsQuery.isFetchingNextPage}
            onEndReached={() => documentsQuery.fetchNextPage()}
            renderItem={(doc) =>
              viewMode === "list" ? (
                <DocumentRow
                  doc={doc}
                  selected={selectedIds.has(doc.id)}
                  onToggleSelect={onToggleSelect}
                  onPreview={onPreview}
                  canRetry={Boolean(source.retryDownload)}
                  canScan={Boolean(source.sendToScanner)}
                  retrying={retryingId === doc.id}
                  scanning={scanningId === doc.id}
                  onRetry={(d) => { setRetryingId(d.id); retryMutation.mutate(d.id); }}
                  onScan={(d) => { setScanningId(d.id); scannerMutation.mutate(d.id); }}
                  canDelete={Boolean(source.deleteDocument)}
                  deleting={deletingId === doc.id}
                  onDelete={(d) => setDeleteTarget(d)}
                />
              ) : (
                <DocumentTile
                  doc={doc}
                  size={viewMode === "large-icons" ? "lg" : "sm"}
                  selected={selectedIds.has(doc.id)}
                  onToggleSelect={onToggleSelect}
                  onPreview={onPreview}
                  canRetry={Boolean(source.retryDownload)}
                  canScan={Boolean(source.sendToScanner)}
                  canDelete={Boolean(source.deleteDocument)}
                  retrying={retryingId === doc.id}
                  scanning={scanningId === doc.id}
                  deleting={deletingId === doc.id}
                  onRetry={(d) => { setRetryingId(d.id); retryMutation.mutate(d.id); }}
                  onScan={(d) => { setScanningId(d.id); scannerMutation.mutate(d.id); }}
                  onDelete={(d) => setDeleteTarget(d)}
                />
              )
            }
          />
        )}
      </div>

      <DeleteDocumentDialog
        document={deleteTarget}
        open={deleteTarget !== null}
        onOpenChange={(next) => { if (!next) setDeleteTarget(null); }}
        pending={deleteMutation.isPending}
        onConfirm={() => {
          if (!deleteTarget) return;
          setDeletingId(deleteTarget.id);
          deleteMutation.mutate(deleteTarget.id);
        }}
      />
    </section>
  );
}
