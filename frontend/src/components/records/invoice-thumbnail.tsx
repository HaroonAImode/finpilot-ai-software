/**
 * Saved Records' per-row thumbnail + click-to-pin lightbox
 * (docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md §6).
 *
 * Always fetches page 0 rendered to PNG (`GET /invoices/{id}/page/0`) rather
 * than the raw source bytes — one code path covers both an image-sourced and
 * a PDF-sourced invoice, since the render endpoint already normalizes either
 * into a PNG (the same endpoint the bounding-box review UI uses). That
 * endpoint calls AI Engine, so this thumbnail (and the lightbox built on it)
 * needs AI Engine running, not just Invoice Service.
 *
 * `smallHoverPreview` switches which hover behavior applies, and the two
 * must never both be active: while nothing is pinned in the right pane,
 * hovering a row live-updates that large panel instead (app.records.tsx's
 * `hoveredId`) — so this thumbnail shows no tooltip of its own then, or the
 * two previews would show the same document twice in two different sizes.
 * Once something IS pinned, swapping the whole right pane on every hover
 * caused real layout thrash (fields jumping, unable to scroll smoothly) —
 * so in that state the panel stays put and this thumbnail's own small
 * tooltip carries the hover preview instead.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileWarning, Loader2 } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchInvoicePage } from "@/lib/invoice-service";

export function InvoiceThumbnail({
  invoiceId, label, smallHoverPreview = false,
}: {
  invoiceId: string;
  label: string;
  smallHoverPreview?: boolean;
}) {
  const [lightboxOpen, setLightboxOpen] = useState(false);

  // staleTime: Infinity — the source file behind an invoice id never
  // changes once scanned, so a re-render/re-fetch of the same thumbnail is
  // pure waste, and hovering or clicking the same row twice must not
  // re-request it.
  const pageQuery = useQuery({
    queryKey: ["invoice-thumbnail", invoiceId],
    queryFn: () => fetchInvoicePage(invoiceId, 0),
    staleTime: Infinity,
    retry: false,
  });
  const objectUrl = useObjectUrl(pageQuery.data);

  const thumb = (
    <span
      role={objectUrl ? "button" : undefined}
      tabIndex={objectUrl ? 0 : undefined}
      onClick={objectUrl ? (e) => { e.stopPropagation(); setLightboxOpen(true); } : undefined}
      onKeyDown={objectUrl ? (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.stopPropagation();
          setLightboxOpen(true);
        }
      } : undefined}
      title={objectUrl ? "Click for a larger preview" : undefined}
      className={`grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-lg border bg-muted/40 ${
        objectUrl ? "cursor-zoom-in" : ""
      }`}
    >
      {pageQuery.isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
      {pageQuery.isError && <FileWarning className="h-3.5 w-3.5 text-muted-foreground" />}
      {objectUrl && <img src={objectUrl} alt="" className="h-full w-full object-cover" />}
    </span>
  );

  return (
    <>
      {smallHoverPreview && objectUrl ? (
        <Tooltip delayDuration={150}>
          <TooltipTrigger asChild>{thumb}</TooltipTrigger>
          <TooltipContent side="right" className="border bg-background p-1.5 shadow-xl">
            <img src={objectUrl} alt={label} className="max-h-64 max-w-64 rounded-md object-contain" />
          </TooltipContent>
        </Tooltip>
      ) : (
        thumb
      )}

      {objectUrl && (
        <Dialog open={lightboxOpen} onOpenChange={setLightboxOpen}>
          <DialogContent className="max-w-3xl">
            <DialogTitle className="truncate pr-6">{label}</DialogTitle>
            <img src={objectUrl} alt={label} className="max-h-[75vh] w-full rounded-lg object-contain" />
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
