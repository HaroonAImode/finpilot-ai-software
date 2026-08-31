import { Eye, RefreshCw, Send, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { FileThumbnail } from "@/components/documents/file-thumbnail";
import { formatBytes, type UnifiedDocument } from "@/lib/documents";

/**
 * One document's row — a single compact line (icon, filename, badges, date/
 * size, actions as icon buttons), not a multi-line card. This used to stack
 * checkbox+name, then badges, then full-width action buttons across three
 * separate lines per file — fine for three files, unusable as a "look
 * through my documents" view once there are hundreds. A real file manager's
 * list/details view is one line per file; this now is too.
 *
 * Shared between the flat list and the grouped-by-conversation view so the two
 * views can never drift into showing different information for the same file.
 */
export function DocumentRow({
  doc,
  selected,
  onToggleSelect,
  onPreview,
  canRetry,
  canScan,
  retrying,
  scanning,
  onRetry,
  onScan,
  canDelete = false,
  deleting = false,
  onDelete,
}: {
  doc: UnifiedDocument;
  selected: boolean;
  onToggleSelect: (doc: UnifiedDocument) => void;
  onPreview: (doc: UnifiedDocument) => void;
  canRetry: boolean;
  canScan: boolean;
  retrying: boolean;
  scanning: boolean;
  onRetry: (doc: UnifiedDocument) => void;
  onScan: (doc: UnifiedDocument) => void;
  /** Only the browser-upload source can delete — a connector's documents
   *  mirror what is in Slack or a mailbox, so removing our copy would just
   *  reappear on the next sync. */
  canDelete?: boolean;
  deleting?: boolean;
  onDelete?: (doc: UnifiedDocument) => void;
}) {
  return (
    <article
      className={`group flex w-full items-center gap-3 rounded-xl border px-3 py-2 transition-colors ${
        selected ? "border-primary bg-primary/5" : "hover:bg-muted/40"
      }`}
    >
      <Checkbox checked={selected} onCheckedChange={() => onToggleSelect(doc)} aria-label={`Select ${doc.filename}`} />

      <FileThumbnail document={doc} onPreview={onPreview} />

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={doc.filename}>{doc.filename}</p>
        <p className="truncate text-xs text-muted-foreground">
          {formatBytes(doc.size)} · {new Date(doc.createdAt).toLocaleDateString()}
        </p>
      </div>

      <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
        <Badge variant="secondary" className="text-[11px] font-normal">{doc.category}</Badge>
        {doc.downloadFailed && <Badge variant="outline" className="text-[11px] text-destructive">Failed</Badge>}
        {!doc.downloaded && !doc.downloadFailed && (
          <Badge variant="outline" className="text-[11px] text-muted-foreground">Pending</Badge>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button" onClick={() => onPreview(doc)} disabled={!doc.contentUrl}
          title={doc.contentUrl ? "Preview this document" : "Not downloaded yet"}
          className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        >
          <Eye className="h-4 w-4" />
        </button>

        {doc.downloadFailed && canRetry && (
          <button
            type="button" onClick={() => onRetry(doc)} disabled={retrying} title="Retry this download"
            className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
          >
            <RefreshCw className={`h-4 w-4 ${retrying ? "animate-spin" : ""}`} />
          </button>
        )}

        {doc.scannerEligible && doc.downloaded && canScan && (
          <button
            type="button" onClick={() => onScan(doc)} disabled={scanning}
            title={scanning ? "Sending…" : "Send to AI Scanner"}
            className="grid h-8 w-8 place-items-center rounded-lg text-primary transition-colors hover:bg-primary/10 disabled:pointer-events-none disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        )}

        {canDelete && onDelete && (
          <button
            type="button" onClick={() => onDelete(doc)} disabled={deleting} title="Remove this document"
            className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground disabled:pointer-events-none disabled:opacity-40"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    </article>
  );
}
