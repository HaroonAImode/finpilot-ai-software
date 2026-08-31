import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronRight, Hash, Loader2, RefreshCw, User } from "lucide-react";
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { DocumentRow } from "@/components/documents/document-row";
import {
  type DocumentSource, type UnifiedConversation, type UnifiedDocument,
} from "@/lib/documents";

/**
 * One collapsible section: a channel or DM, and the documents inside it.
 *
 * Files load lazily, only once the section is expanded (`enabled: isOpen`).
 * A workspace can hold thousands of files across dozens of conversations —
 * fetching every group's contents up front would just move the
 * "sync everything at once" problem from the write path to the read path,
 * which this whole selective-sync effort exists to avoid.
 */
export function ConversationGroup({
  source,
  conversation,
  category,
  search,
  onPreview,
  selectedIds,
  onToggleSelect,
  onSelectMany,
  canDelete = false,
  onDeleteRequest,
  defaultOpen = false,
}: {
  source: DocumentSource;
  conversation: UnifiedConversation;
  category: string | undefined;
  search: string;
  onPreview: (doc: UnifiedDocument) => void;
  selectedIds: Set<string>;
  onToggleSelect: (doc: UnifiedDocument) => void;
  onSelectMany: (docs: UnifiedDocument[], selected: boolean) => void;
  canDelete?: boolean;
  /** Opens the shared delete-confirmation dialog one level up in
   *  SourceColumn — the mutation and dialog live there so a grouped and a
   *  flat view of the same source never end up with two independent
   *  delete flows to keep in sync. */
  onDeleteRequest?: (doc: UnifiedDocument) => void;
  defaultOpen?: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(defaultOpen);
  const [scanningId, setScanningId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  const documentsQuery = useQuery({
    queryKey: ["source-documents", source.id, "conversation", conversation.id, category],
    queryFn: () =>
      source.fetchDocuments!({
        skip: 0,
        limit: 100,
        conversationId: conversation.id,
        ...(category ? { category } : {}),
      }),
    enabled: open && Boolean(source.fetchDocuments),
  });

  const scannerMutation = useMutation({
    mutationFn: (documentId: string) => source.sendToScanner!(documentId),
    onSuccess: () => toast.success("Sent to AI Scanner — check the Scanner page for progress"),
    onError: (error: Error) => toast.error(error.message || "Could not send this document to the scanner"),
    onSettled: () => setScanningId(null),
  });

  const retryMutation = useMutation({
    mutationFn: (documentId: string) => source.retryDownload!(documentId),
    onSuccess: () => {
      toast.success("Re-queued — the worker will retry this download");
      queryClient.invalidateQueries({
        queryKey: ["source-documents", source.id, "conversation", conversation.id],
      });
    },
    onError: (error: Error) => toast.error(error.message || "Could not retry this download"),
    onSettled: () => setRetryingId(null),
  });

  // windowDays is deliberately omitted (not sent as null) — a one-click sync
  // from a channel header should respect whatever date window is already
  // saved for the installation, not silently reset it to All Time the way an
  // explicit picker choice would.
  const syncMutation = useMutation({
    mutationFn: () => source.startSync!({ conversationIds: [conversation.id] }),
    onSuccess: () => {
      toast(`Sync started for ${conversation.name}`);
      queryClient.invalidateQueries({ queryKey: ["source-conversations", source.id] });
      queryClient.invalidateQueries({
        queryKey: ["source-documents", source.id, "conversation", conversation.id],
      });
    },
    onError: (error: Error) => toast.error(error.message || "Could not start sync"),
  });

  const allDocuments = documentsQuery.data?.documents ?? [];
  const documents = allDocuments.filter((doc) =>
    `${doc.filename} ${doc.title ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );
  const allSelected = documents.length > 0 && documents.every((doc) => selectedIds.has(doc.id));
  const hasError = Boolean(conversation.errorHint);

  const Icon = conversation.kind === "dm" ? User : Hash;

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="overflow-hidden rounded-xl border">
      <div className={`flex items-center gap-1.5 p-3 ${hasError ? "bg-destructive/5" : ""}`}>
        {/* The sync button is a sibling, not a child, of this trigger button —
            nesting an interactive control inside another <button> is invalid
            HTML and Radix's asChild would merge both click handlers into one
            element anyway. */}
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left transition-colors hover:bg-muted/40"
          >
            <ChevronRight
              className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
            />
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-muted">
              <Icon className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={conversation.name}>{conversation.name}</p>
              <p className="text-xs text-muted-foreground">
                {conversation.fileCount} file{conversation.fileCount === 1 ? "" : "s"}
                {conversation.lastSynced && ` · synced ${new Date(conversation.lastSynced).toLocaleDateString()}`}
              </p>
            </div>
          </button>
        </CollapsibleTrigger>
        {hasError && (
          <Badge variant="outline" className="shrink-0 gap-1 text-[11px] text-destructive">
            <AlertTriangle className="h-3 w-3" /> Needs attention
          </Badge>
        )}
        {source.startSync && (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-muted-foreground"
            title={`Sync ${conversation.name}`}
            aria-label={`Sync ${conversation.name}`}
            disabled={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncMutation.isPending ? "animate-spin" : ""}`} />
          </Button>
        )}
      </div>

      <CollapsibleContent>
        <div className="space-y-2 border-t p-3">
          {hasError && (
            <p className="rounded-lg bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {conversation.errorHint}
            </p>
          )}

          {documentsQuery.isPending && (
            <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading files…
            </div>
          )}

          {documentsQuery.isError && (
            <p className="py-4 text-center text-sm text-destructive">
              {(documentsQuery.error as Error)?.message ?? "Could not load these files."}
            </p>
          )}

          {!documentsQuery.isPending && !documentsQuery.isError && documents.length === 0 && !hasError && (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {conversation.fileCount === 0 ? "No files here yet." : "Nothing matches these filters."}
            </p>
          )}

          {documents.length > 0 && (
            <label className="flex cursor-pointer items-center gap-1.5 pb-1 text-xs text-muted-foreground">
              <Checkbox
                checked={allSelected}
                onCheckedChange={(checked) => onSelectMany(documents, checked === true)}
                aria-label={`Select all files in ${conversation.name}`}
              />
              Select all in {conversation.name}
            </label>
          )}

          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
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
              canDelete={canDelete}
              {...(onDeleteRequest ? { onDelete: onDeleteRequest } : {})}
            />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
