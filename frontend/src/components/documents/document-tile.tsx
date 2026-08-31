/**
 * One file as a tile — the icon-grid views' unit (`app.documents.tsx`'s
 * Small Icons / Large Icons view modes). Two distinct interactions, same
 * split as Saved Records' invoice thumbnail
 * (components/records/invoice-thumbnail.tsx):
 * - Hover (images only, once downloaded) → an instant larger preview via a
 *   Tooltip anchored to the icon. Radix positions this next to the trigger,
 *   not at the cursor, and closes it the instant the pointer leaves — it
 *   never tracks the mouse or sits on top of content you would otherwise be
 *   scrolling past, so it cannot trap the cursor or block the page under it.
 * - Click the thumbnail → the existing DocumentPreviewDialog, which stays
 *   open until the user closes it (its built-in X, or Escape — both
 *   Radix Dialog defaults, not reimplemented here).
 *
 * The thumbnail itself (image bytes, or a PDF's real first page, or a
 * colored FileTypeIcon for everything else) is FileThumbnail — shared with
 * DocumentRow so a PDF looks the same whether you're in list or icon view.
 *
 * Two distinct layouts, not one tile scaled up: "sm" is a compact row
 * (small icon beside the filename, several per line) matching a file
 * manager's actual small-icon/list view; "lg" is the icon-on-top, name-
 * below square tile a large-icon view is expected to look like. Reusing one
 * shape at two sizes for both was the wrong idea — the layouts genuinely
 * differ, not just the size of a icon.
 */
import { Eye, MoreVertical, RefreshCw, Send, Trash2 } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FileThumbnail } from "@/components/documents/file-thumbnail";
import { formatBytes, type UnifiedDocument } from "@/lib/documents";

function ActionsMenu({
  doc, canRetry, canScan, canDelete, retrying, scanning, deleting, onPreview, onRetry, onScan, onDelete, className,
}: {
  doc: UnifiedDocument;
  canRetry: boolean;
  canScan: boolean;
  canDelete: boolean;
  retrying: boolean;
  scanning: boolean;
  deleting: boolean;
  onPreview: (doc: UnifiedDocument) => void;
  onRetry: (doc: UnifiedDocument) => void;
  onScan: (doc: UnifiedDocument) => void;
  // Not `onDelete?:` — this component always receives the prop explicitly
  // (possibly `undefined`) via a spread from DocumentTile's own optional
  // prop, and exactOptionalPropertyTypes treats an explicit `undefined`
  // value as distinct from the key being entirely absent.
  onDelete: ((doc: UnifiedDocument) => void) | undefined;
  className: string;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" title="Actions" className={className}>
          <MoreVertical className="h-3.5 w-3.5" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => onPreview(doc)} disabled={!doc.contentUrl}>
          <Eye className="mr-2 h-3.5 w-3.5" /> Preview
        </DropdownMenuItem>
        {doc.downloadFailed && canRetry && (
          <DropdownMenuItem onSelect={() => onRetry(doc)} disabled={retrying}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" /> Retry
          </DropdownMenuItem>
        )}
        {doc.scannerEligible && doc.downloaded && canScan && (
          <DropdownMenuItem onSelect={() => onScan(doc)} disabled={scanning}>
            <Send className="mr-2 h-3.5 w-3.5" /> Send to Scanner
          </DropdownMenuItem>
        )}
        {canDelete && onDelete && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => onDelete(doc)} disabled={deleting}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="mr-2 h-3.5 w-3.5" /> Delete
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function DocumentTile({
  doc, size, selected, onToggleSelect, onPreview,
  canRetry, canScan, canDelete, retrying, scanning, deleting,
  onRetry, onScan, onDelete,
}: {
  doc: UnifiedDocument;
  size: "sm" | "lg";
  selected: boolean;
  onToggleSelect: (doc: UnifiedDocument) => void;
  onPreview: (doc: UnifiedDocument) => void;
  canRetry: boolean;
  canScan: boolean;
  canDelete: boolean;
  retrying: boolean;
  scanning: boolean;
  deleting: boolean;
  onRetry: (doc: UnifiedDocument) => void;
  onScan: (doc: UnifiedDocument) => void;
  onDelete?: (doc: UnifiedDocument) => void;
}) {
  const actionsMenuProps = { doc, canRetry, canScan, canDelete, retrying, scanning, deleting, onPreview, onRetry, onScan, onDelete };

  if (size === "sm") {
    // A compact row: small icon, filename beside it, size — several of
    // these sit side by side per virtualized row (source-column.tsx),
    // matching a real file manager's small-icon/list view rather than a
    // shrunken version of the large-icon tile below.
    return (
      <div className={`group flex w-full items-center gap-2 rounded-lg px-1.5 py-1 transition-colors ${selected ? "bg-primary/5" : "hover:bg-muted/40"}`}>
        <Checkbox
          checked={selected} onCheckedChange={() => onToggleSelect(doc)}
          aria-label={`Select ${doc.filename}`}
          className={`shrink-0 transition-opacity ${selected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}
        />
        <FileThumbnail document={doc} onPreview={onPreview} size="sm" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={doc.filename}>{doc.filename}</span>
        <ActionsMenu
          {...actionsMenuProps}
          className="shrink-0 rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100"
        />
      </div>
    );
  }

  // Large icons: icon on top, name below, the square-tile shape people
  // expect from macOS Finder's / Windows' own large-icon view.
  return (
    <div className={`group relative flex w-full flex-col items-center gap-1.5 rounded-2xl p-2 text-center transition-colors ${selected ? "bg-primary/5" : "hover:bg-muted/30"}`}>
      <div className={`absolute left-1.5 top-1.5 z-10 transition-opacity ${selected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
        <Checkbox
          checked={selected} onCheckedChange={() => onToggleSelect(doc)}
          aria-label={`Select ${doc.filename}`} className="bg-background"
        />
      </div>

      <ActionsMenu
        {...actionsMenuProps}
        className="absolute right-1.5 top-1.5 z-10 rounded-md bg-background/90 p-1 opacity-0 shadow-sm transition-opacity hover:bg-muted group-hover:opacity-100"
      />

      <FileThumbnail document={doc} onPreview={onPreview} size="lg" />

      <div className="w-full text-sm">
        <p className="truncate font-medium" title={doc.filename}>{doc.filename}</p>
        <p className="truncate text-[11px] text-muted-foreground">{formatBytes(doc.size)}</p>
      </div>
    </div>
  );
}
