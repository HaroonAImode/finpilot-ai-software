import { useState } from "react";
import { Code2, Download, ExternalLink, Globe } from "lucide-react";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DocumentPreviewBody, isHtmlPreview } from "@/components/documents/document-preview-body";
import { formatBytes, type UnifiedDocument } from "@/lib/documents";

/**
 * Renders a stored document inline instead of bouncing the user out to the
 * source app or a download. The fetch-and-render engine lives in
 * DocumentPreviewBody, shared with the delete-confirmation dialog's small
 * preview — this component owns only the chrome around it: header, the
 * Page/Source toggle for HTML, and the Open/Download footer.
 */
export function DocumentPreviewDialog({
  document, open, onOpenChange,
}: {
  document: UnifiedDocument | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [htmlView, setHtmlView] = useState<"page" | "source">("page");
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  if (!document) return null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setHtmlView("page");
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[92vh] w-[96vw] max-w-5xl flex-col gap-0 p-0">
        <DialogHeader className="space-y-1 border-b px-5 py-4">
          <DialogTitle className="truncate pr-8 text-base" title={document.filename}>
            {document.filename}
          </DialogTitle>
          {/* DialogDescription renders a <p>, and a <p> cannot legally contain a
              <div> — which is what Badge is. Keeping the description to plain
              text and putting the badge in a sibling row avoids the invalid
              nesting that was breaking hydration. */}
          <DialogDescription className="text-xs">
            {document.fileType} · {formatBytes(document.size)} ·{" "}
            {new Date(document.createdAt).toLocaleDateString()}
            {document.sharedBy ? ` · shared by ${document.sharedBy}` : ""}
          </DialogDescription>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Badge variant="outline" className="capitalize">{document.source}</Badge>
            <Badge variant="secondary" className="font-normal">{document.category}</Badge>
            {document.categorySource === "manual_override" && (
              <span className="text-xs text-muted-foreground">edited by you</span>
            )}

            {/* An HTML invoice is a document to read, not markup to scan — but
                the markup is occasionally what you want, so both are one click
                apart instead of one being the only option. */}
            {isHtmlPreview(document) && (
              <div className="ml-auto flex items-center gap-1 rounded-lg border p-0.5">
                <Button
                  size="sm"
                  variant={htmlView === "page" ? "secondary" : "ghost"}
                  className="h-7 gap-1.5 rounded-md px-2.5 text-xs"
                  onClick={() => setHtmlView("page")}
                >
                  <Globe className="h-3.5 w-3.5" /> Page
                </Button>
                <Button
                  size="sm"
                  variant={htmlView === "source" ? "secondary" : "ghost"}
                  className="h-7 gap-1.5 rounded-md px-2.5 text-xs"
                  onClick={() => setHtmlView("source")}
                >
                  <Code2 className="h-3.5 w-3.5" /> Source
                </Button>
              </div>
            )}
          </div>
        </DialogHeader>

        <DocumentPreviewBody
          document={document} open={open} htmlView={htmlView} onReady={setDownloadUrl}
        />

        <PreviewFooter document={document} downloadUrl={downloadUrl} />
      </DialogContent>
    </Dialog>
  );
}

function PreviewFooter({
  document, downloadUrl,
}: {
  document: UnifiedDocument;
  downloadUrl: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 border-t px-5 py-3">
      {document.externalUrl && (
        <Button variant="ghost" size="sm" className="gap-1.5" asChild>
          <a href={document.externalUrl} target="_blank" rel="noreferrer">
            <ExternalLink className="h-3.5 w-3.5" /> Open in {document.source}
          </a>
        </Button>
      )}
      {downloadUrl && (
        // From the object URL DocumentPreviewBody reported via onReady, for
        // the same reason the preview itself does not point straight at
        // /content: a plain link would carry no Authorization header.
        <Button variant="outline" size="sm" className="gap-1.5 rounded-xl" asChild>
          <a href={downloadUrl} download={document.filename}>
            <Download className="h-3.5 w-3.5" /> Download
          </a>
        </Button>
      )}
    </div>
  );
}
