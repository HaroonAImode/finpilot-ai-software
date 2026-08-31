import { Loader2 } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { DocumentPreviewBody } from "@/components/documents/document-preview-body";
import { formatBytes, type UnifiedDocument } from "@/lib/documents";

/**
 * Confirms a delete with a small preview of the actual document, not just
 * its filename — the whole point is letting someone recognise a document
 * they no longer want (a stray project report or design file pulled in
 * from Slack/Email) before it disappears, rather than trusting a filename
 * alone to be enough.
 *
 * Every source's delete is a soft delete (see DocumentSource.deleteDocument's
 * docstring): Slack, the mailbox, and the original file are never touched,
 * only FinPilot's own record of it.
 */
export function DeleteDocumentDialog({
  document, open, onOpenChange, onConfirm, pending,
}: {
  document: UnifiedDocument | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  if (!document) return null;

  return (
    <AlertDialog open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this document?</AlertDialogTitle>
          <AlertDialogDescription>
            This only removes it from FinPilot — the original in {document.source} is untouched,
            and it won't come back on the next sync.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium" title={document.filename}>
              {document.filename}
            </p>
            <Badge variant="secondary" className="shrink-0 font-normal">{document.category}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            {document.fileType} · {formatBytes(document.size)}
          </p>
          <div className="overflow-hidden rounded-lg border">
            <DocumentPreviewBody document={document} open={open} compact downloadable={false} />
          </div>
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel className="rounded-xl" disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className="rounded-xl bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={pending}
            onClick={(event) => {
              // The primitive closes on click by default — but this action
              // is async, so closing has to wait for onConfirm's mutation
              // to actually settle, not fire the moment the button is pressed.
              event.preventDefault();
              onConfirm();
            }}
          >
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
