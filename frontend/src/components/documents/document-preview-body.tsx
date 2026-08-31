import { useEffect, useState } from "react";
import { AlertCircle, Download, FileQuestion, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSource, previewKindFor, type UnifiedDocument } from "@/lib/documents";

/**
 * The fetch-and-render engine shared by the full preview dialog and the
 * delete-confirmation dialog's small preview — extracted so both render the
 * same bytes the same way instead of one reimplementing a second, likely
 * drifting copy of the mimetype-branching logic below.
 *
 * See DocumentPreviewDialog's original docstring for why bytes are fetched
 * and turned into an object URL rather than pointing an iframe/img straight
 * at the streaming endpoint: neither can carry the Authorization header an
 * authenticated route requires.
 */
export function DocumentPreviewBody({
  document, open, compact = false, htmlView = "page", downloadable = true, onReady,
}: {
  document: UnifiedDocument;
  open: boolean;
  /** Shrinks the rendered preview for a confirmation dialog, where the
   *  point is "does this look like the right file", not full reading. */
  compact?: boolean;
  /** Owned by the caller: the full dialog renders the Page/Source toggle in
   *  its own header, so this component just reflects whichever is chosen
   *  rather than owning that control itself. The compact preview never
   *  passes this — it always shows the rendered page. */
  htmlView?: "page" | "source";
  /** Whether to show a Download fallback for non-renderable file types.
   *  Off in the compact preview: a confirmation dialog is not the place to
   *  hand out a copy of the thing you are about to delete. */
  downloadable?: boolean;
  /** Reports the fetched download URL up once it is ready (null while
   *  loading/unavailable), so a caller's own footer — e.g. the full
   *  dialog's Download button — can offer it without a second fetch of
   *  the same bytes. */
  onReady?: (downloadUrl: string | null) => void;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [sourceText, setSourceText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const documentId = document.id;
  const fetchBlob = getSource(document.source)?.fetchBlob;
  const canFetch = Boolean(open && document.contentUrl && fetchBlob);
  const kind = previewKindFor(document.mimetype, document.filename);

  useEffect(() => {
    if (!canFetch || !fetchBlob) {
      setObjectUrl(null);
      setDownloadUrl(null);
      setSourceText(null);
      setError(null);
      return;
    }

    let cancelled = false;
    let created: string | null = null;
    let createdDownload: string | null = null;

    setLoading(true);
    setError(null);

    fetchBlob(documentId)
      .then(async (blob) => {
        if (cancelled) return;

        if (kind === "html") {
          const text = await blob.text();
          if (cancelled) return;
          setSourceText(text);
          created = URL.createObjectURL(new Blob([text], { type: "text/html" }));
        } else if (kind === "docx") {
          const { convertToHtml } = await import("mammoth");
          const result = await convertToHtml({ arrayBuffer: await blob.arrayBuffer() });
          if (cancelled) return;
          created = URL.createObjectURL(new Blob([result.value], { type: "text/html" }));
          createdDownload = URL.createObjectURL(blob);
        } else {
          created = URL.createObjectURL(blob);
        }
        setObjectUrl(created);
        setDownloadUrl(createdDownload ?? created);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message || "Could not load this document");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
      if (createdDownload) URL.revokeObjectURL(createdDownload);
    };
  }, [canFetch, documentId, kind, fetchBlob]);

  useEffect(() => {
    onReady?.(downloadUrl);
  }, [downloadUrl, onReady]);

  const renderable = kind !== "unsupported";
  const frameHeight = compact ? "h-[30vh]" : "h-[70vh]";
  const minHeight = compact ? "min-h-[160px]" : "min-h-[55vh]";

  return (
    <div className={`relative ${minHeight} flex-1 overflow-auto bg-muted/30`}>
      {!document.contentUrl && (
        <div className="grid h-full place-items-center p-6 text-center">
          <div className="flex flex-col items-center gap-2">
            <FileQuestion className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm font-medium">Not downloaded yet</p>
            {!compact && (
              <p className="max-w-sm text-xs text-muted-foreground">
                {document.downloadFailed
                  ? "The download failed. Retry it from the file list, then preview again."
                  : "This document has not finished syncing from its source."}
              </p>
            )}
          </div>
        </div>
      )}

      {document.contentUrl && loading && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex flex-col items-center gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            {!compact && <p className="text-sm">Loading preview…</p>}
          </div>
        </div>
      )}

      {document.contentUrl && error && !loading && (
        <div className="grid h-full place-items-center p-6 text-center">
          <div className="flex flex-col items-center gap-2">
            <AlertCircle className="h-6 w-6 text-destructive" />
            <p className="text-sm font-medium">Could not load this document</p>
            {!compact && <p className="max-w-sm text-xs text-muted-foreground">{error}</p>}
          </div>
        </div>
      )}

      {objectUrl && !error && !renderable && (
        <div className="grid h-full place-items-center p-6 text-center">
          <div className="flex flex-col items-center gap-2">
            <FileQuestion className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm font-medium">
              No inline preview for {document.fileType.toUpperCase()} files
            </p>
            {downloadable && (
              <Button asChild size="sm" variant="outline" className="mt-1 gap-1.5 rounded-xl">
                <a href={downloadUrl ?? objectUrl} download={document.filename}>
                  <Download className="h-3.5 w-3.5" /> Download
                </a>
              </Button>
            )}
          </div>
        </div>
      )}

      {objectUrl && !error && (kind === "pdf" || kind === "text") && (
        <iframe
          src={objectUrl}
          title={`Preview of ${document.filename}`}
          className={`${frameHeight} w-full border-0 bg-background`}
        />
      )}

      {objectUrl && !error && kind === "html" && (compact || htmlView === "page") && (
        // sandbox with nothing allowed: this document came from an
        // untrusted source (Slack/Email). Styling and layout still render,
        // but scripts cannot run and it has no access to the app around it.
        <iframe
          src={objectUrl}
          title={`Preview of ${document.filename}`}
          sandbox=""
          referrerPolicy="no-referrer"
          className={`${frameHeight} w-full border-0 bg-white`}
        />
      )}

      {objectUrl && !error && kind === "docx" && (
        <iframe
          src={objectUrl}
          title={`Preview of ${document.filename}`}
          sandbox=""
          referrerPolicy="no-referrer"
          className={`${frameHeight} w-full border-0 bg-white`}
        />
      )}

      {!compact && sourceText !== null && !error && kind === "html" && htmlView === "source" && (
        <pre className="max-h-[70vh] overflow-auto bg-background p-4 text-xs leading-relaxed">
          <code>{sourceText}</code>
        </pre>
      )}

      {objectUrl && !error && kind === "image" && (
        <div className={`grid ${compact ? "min-h-[160px]" : "min-h-[55vh]"} place-items-center p-4`}>
          <img
            src={objectUrl}
            alt={document.title ?? document.filename}
            className={`${compact ? "max-h-[26vh]" : "max-h-[70vh]"} max-w-full rounded-lg object-contain shadow-sm`}
          />
        </div>
      )}
    </div>
  );
}

/** Whether this document's preview is an HTML page with a Page/Source
 *  toggle worth showing. Exported so the full dialog's header knows when
 *  to render that control, without re-deriving previewKindFor itself. */
export function isHtmlPreview(document: UnifiedDocument): boolean {
  return previewKindFor(document.mimetype, document.filename) === "html";
}
