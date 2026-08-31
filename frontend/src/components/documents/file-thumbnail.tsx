/**
 * A real visual thumbnail wherever one is actually achievable client-side —
 * an image's own bytes, or a PDF's first page rendered via pdf.js
 * (lib/pdf-thumbnail.ts) — falling back to FileTypeIcon everywhere else
 * (Word, zip, ...), the same way a real file manager only bothers
 * rasterizing the formats it actually has a renderer for.
 *
 * Shared by DocumentRow (list view) and DocumentTile (icon views) so a PDF
 * shows the same real page-1 thumbnail everywhere it appears, computed
 * once per document per component instance.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileTypeIcon } from "@/components/documents/file-type-icon";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useObjectUrl } from "@/hooks/use-object-url";
import { renderPdfFirstPageThumbnail } from "@/lib/pdf-thumbnail";
import { fileKindFor, getSource, type UnifiedDocument } from "@/lib/documents";

export function FileThumbnail({
  document, onPreview, size = "sm",
}: {
  document: UnifiedDocument;
  onPreview: (doc: UnifiedDocument) => void;
  size?: "sm" | "lg";
}) {
  const kind = fileKindFor(document.mimetype, document.filename);
  const fetchBlob = getSource(document.source)?.fetchBlob;
  const canRender = (kind === "image" || kind === "pdf") && document.downloaded && Boolean(fetchBlob);

  // staleTime: Infinity — a stored file's bytes never change, so this must
  // never re-fetch just because the row re-rendered.
  const blobQuery = useQuery({
    queryKey: ["file-thumbnail-blob", document.source, document.id],
    queryFn: () => fetchBlob!(document.id),
    enabled: canRender,
    staleTime: Infinity,
    retry: false,
  });

  const imageUrl = useObjectUrl(kind === "image" ? blobQuery.data : undefined);

  const [pdfThumbUrl, setPdfThumbUrl] = useState<string | null>(null);
  useEffect(() => {
    if (kind !== "pdf" || !blobQuery.data) {
      setPdfThumbUrl(null);
      return;
    }
    let cancelled = false;
    renderPdfFirstPageThumbnail(blobQuery.data).then((url) => {
      if (!cancelled) setPdfThumbUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [kind, blobQuery.data]);

  const previewUrl = kind === "image" ? imageUrl : pdfThumbUrl;
  const box = size === "lg" ? "h-28 w-28 rounded-2xl" : "h-9 w-9 rounded-lg";
  const tooltipMax = size === "lg" ? "max-h-72 max-w-72" : "max-h-64 max-w-64";

  const thumb = (
    <button
      type="button" onClick={() => onPreview(document)} disabled={!document.contentUrl}
      className={`grid ${box} shrink-0 place-items-center overflow-hidden border bg-muted/30 transition-colors ${
        document.contentUrl ? "cursor-zoom-in hover:bg-muted/50" : "cursor-not-allowed opacity-60"
      }`}
      title={document.contentUrl ? "Click for a larger preview" : "Not downloaded yet"}
    >
      {previewUrl ? (
        <img src={previewUrl} alt="" className="h-full w-full object-cover" />
      ) : (
        <FileTypeIcon kind={kind} size={size} />
      )}
    </button>
  );

  if (!previewUrl) return thumb;

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>{thumb}</TooltipTrigger>
      <TooltipContent side="right" className="border bg-background p-1.5 shadow-xl">
        <img src={previewUrl} alt={document.filename} className={`${tooltipMax} rounded-md object-contain`} />
      </TooltipContent>
    </Tooltip>
  );
}
