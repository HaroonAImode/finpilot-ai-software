
/**
 * The right pane's full document preview for a selected Saved Records entry
 * (docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md §6).
 *
 * Renders through `GET /invoices/{id}/page/{n}` (the same rasterize-to-PNG
 * endpoint InvoiceThumbnail uses) rather than streaming the raw file into
 * an `<iframe>`. That used to mean a PDF-sourced invoice opened in the
 * browser's own native PDF viewer — its own toolbar, its own page-thumbnail
 * rail, zoom/rotate/print controls nobody asked for — a different, heavier
 * "preview" than an image ever got. Routing both through the same
 * rasterizer means a PDF and an image now look like the same kind of
 * preview: one clean page, full width, no browser chrome. Multi-page PDFs
 * still get to more pages — just via an explicit, collapsed-by-default
 * toggle below, not by dropping the whole native viewer on someone by
 * default.
 *
 * Zoom is a real, explicit control (buttons, plus pinch/ctrl-scroll — see
 * below): the image grows to a concrete pixel width inside a frame of fixed
 * height, and the frame's own scrollbars reach the rest, rather than a CSS
 * transform that would just clip or a frame that grows with the image (the
 * outer page used to visibly get taller/shorter as you zoomed — a real bug,
 * not this design; see FRAME_HEIGHT_PX's own comment).
 *
 * Plain mouse-wheel scrolling is still never zoom — deliberately: a wheel
 * that sometimes zooms and sometimes scrolls the page depending on where
 * the cursor happens to be is exactly the unpredictable-scrolling complaint
 * this page already had once. What *is* wired now is the two unambiguous
 * "the user is pinching" signals a browser actually reports: a two-finger
 * touch gesture (`touchmove` with 2 active touches), and a trackpad pinch
 * (which every major browser reports as a `wheel` event with `ctrlKey:
 * true`, indistinguishable from an *actual* held-Ctrl+scroll — so this
 * treats both the same, matching how a PDF viewer or image editor already
 * behaves). Both are wired as native, non-passive listeners (a React
 * `onWheel`/`onTouchMove` prop cannot call `preventDefault()` on these two
 * event types — React attaches them passively by default — so without a
 * real `addEventListener(..., { passive: false })` the browser's own
 * page-zoom would still fire alongside this one).
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Loader2,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchInvoicePage } from "@/lib/invoice-service";

const DEFAULT_ZOOM = 0.5;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;
/** The frame itself never resizes — same footprint at 50% or 300% zoom.
 *  It used to be a `min-h-[420px]` with no matching max, so zooming in grew
 *  the whole box (and everything below it on the page) taller instead of
 *  scrolling within a fixed frame, exactly the "the whole preview gets
 *  bigger/smaller" report. A real, bounded frame is what makes
 *  `overflow-auto` on it actually do anything.
 *
 *  720px — a real, page-sized viewing area (closer to how a printed A4
 *  sheet reads on screen) rather than a cramped 420-480px box a receipt's
 *  actual content had to be scrolled or zoomed just to see in full. */
const FRAME_HEIGHT_PX = 720;

function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, +z.toFixed(2)));
}

function touchDistance(a: Touch, b: Touch): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

function PageThumb({
  invoiceId,
  page,
  active,
  onSelect,
}: {
  invoiceId: string;
  page: number;
  active: boolean;
  onSelect: () => void;
}) {
  // Same queryKey shape as the main preview below — react-query dedupes
  // rather than double-fetching whichever page happens to already be shown.
  const pageQuery = useQuery({
    queryKey: ["invoice-preview-page", invoiceId, page],
    queryFn: () => fetchInvoicePage(invoiceId, page),
    staleTime: Infinity,
    retry: false,
  });
  const url = useObjectUrl(pageQuery.data);

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`shrink-0 overflow-hidden rounded-lg border-2 transition-colors ${
        active ? "border-primary" : "border-transparent hover:border-muted-foreground/30"
      }`}
      title={`Page ${page + 1}`}
    >
      <span className="grid h-16 w-12 place-items-center bg-muted/50">
        {url ? (
          <img src={url} alt="" className="h-full w-full object-cover" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        )}
      </span>
      <span className="block bg-background py-0.5 text-center text-[10px] text-muted-foreground">
        {page + 1}
      </span>
    </button>
  );
}

export function InvoiceDocumentPreview({
  invoiceId,
  filename,
  pageCount = 1,
}: {
  invoiceId: string;
  filename: string | null;
  /** Total pages in the source document — Invoice.page_dimensions.length.
   *  Omitted (or 1) simply means no page picker renders; the single-page
   *  preview below always works regardless. */
  pageCount?: number;
}) {
  const [currentPage, setCurrentPage] = useState(0);
  const [showPages, setShowPages] = useState(false);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const frameRef = useRef<HTMLDivElement>(null);
  // Two active touches' starting distance + the zoom at that moment, so a
  // pinch's midpoint delta maps to a zoom *ratio* rather than jumping by a
  // fixed step per event — same reason a real image viewer's pinch feels
  // smooth instead of stepping in 25% jumps like the buttons do.
  const pinchRef = useRef<{ distance: number; zoom: number } | null>(null);

  // A fresh page starts at the default 50% zoom.
  useEffect(() => setZoom(DEFAULT_ZOOM), [currentPage]);

  // Native, non-passive listeners — see this file's module docstring for
  // why a React onWheel/onTouchMove prop can't do this (they're passive by
  // default, so preventDefault() on them is silently ignored and the
  // browser's own page-zoom fires anyway).
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;

    function handleWheel(e: WheelEvent) {
      if (!e.ctrlKey) return; // plain scroll — let it scroll the frame, not zoom
      e.preventDefault();
      setZoom((z) => clampZoom(z - e.deltaY * 0.01));
    }

    function handleTouchStart(e: TouchEvent) {
      if (e.touches.length === 2) {
        pinchRef.current = {
          // Just checked `.length === 2` above — both indices exist.
          distance: touchDistance(e.touches[0]!, e.touches[1]!),
          zoom,
        };
      }
    }
    function handleTouchMove(e: TouchEvent) {
      if (e.touches.length !== 2 || !pinchRef.current) return;
      e.preventDefault();
      // Just checked `.length === 2` above — both indices exist.
      const distance = touchDistance(e.touches[0]!, e.touches[1]!);
      setZoom(clampZoom(pinchRef.current.zoom * (distance / pinchRef.current.distance)));
    }
    function handleTouchEnd(e: TouchEvent) {
      if (e.touches.length < 2) pinchRef.current = null;
    }

    frame.addEventListener("wheel", handleWheel, { passive: false });
    frame.addEventListener("touchstart", handleTouchStart, { passive: true });
    frame.addEventListener("touchmove", handleTouchMove, { passive: false });
    frame.addEventListener("touchend", handleTouchEnd, { passive: true });
    return () => {
      frame.removeEventListener("wheel", handleWheel);
      frame.removeEventListener("touchstart", handleTouchStart);
      frame.removeEventListener("touchmove", handleTouchMove);
      frame.removeEventListener("touchend", handleTouchEnd);
    };
    // `zoom` is read fresh into pinchRef only at gesture start, not on every
    // frame — re-subscribing whenever it changes would reset an in-progress
    // pinch's baseline. handleTouchStart's closure over `zoom` is refreshed
    // by this effect re-running after every zoom change anyway (buttons or
    // gestures), which is exactly the up-to-date value a *new* pinch needs.
  }, [zoom]);

  const pageQuery = useQuery({
    queryKey: ["invoice-preview-page", invoiceId, currentPage],
    queryFn: () => fetchInvoicePage(invoiceId, currentPage),
    staleTime: Infinity,
    retry: false,
  });
  const objectUrl = useObjectUrl(pageQuery.data);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={frameRef}
        // `touchAction: pan-x pan-y` (not `none`) — a single finger must
        // still be able to scroll/pan a zoomed-in page around inside this
        // frame; only the browser's own native pinch-to-zoom is disabled
        // here, so it can't fight our own two-finger handler for the same
        // gesture.
        style={{ height: `${FRAME_HEIGHT_PX}px`, touchAction: "pan-x pan-y" }}
        className="relative overflow-auto rounded-xl border bg-muted/30"
      >
        {pageQuery.isLoading && (
          <div className="grid h-full place-items-center">
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              <p className="text-sm">Loading preview…</p>
            </div>
          </div>
        )}

        {pageQuery.isError && !pageQuery.isLoading && (
          <div className="grid h-full place-items-center p-6 text-center">
            <div className="flex flex-col items-center gap-2">
              <AlertCircle className="h-6 w-6 text-destructive" />
              <p className="text-sm font-medium">Could not load this document</p>
            </div>
          </div>
        )}

        {objectUrl && (
          <div className="flex min-h-full justify-center items-start p-4 pt-2">
            {/* Top-aligned and horizontally centered so the receipt starts
             *  directly near the top of the frame. transformOrigin: top center
             *  keeps the top of the document anchored at the top when scaled. */}
            <img
              src={objectUrl}
              alt={filename ?? "Invoice document"}
              style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
              className="max-h-full max-w-full rounded-lg object-contain shadow-sm"
            />
          </div>
        )}

        {objectUrl && (
          // `absolute` positions against the frame itself (its nearest
          // `relative` ancestor), not the frame's scrolled content — so
          // this stays pinned in the same corner of the *frame* no matter
          // how far zoomed content has scrolled inside it.
          <div className="absolute bottom-3 right-3 z-10 flex items-center gap-1 rounded-lg border bg-background/95 p-1 shadow-md backdrop-blur">
            <button
              type="button"
              onClick={() => setZoom((z) => clampZoom(z - ZOOM_STEP))}
              disabled={zoom <= MIN_ZOOM}
              title="Zoom out"
              className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            >
              <ZoomOut className="h-3.5 w-3.5" />
            </button>
            <span className="w-11 text-center text-xs tabular-nums text-muted-foreground">
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              onClick={() => setZoom((z) => clampZoom(z + ZOOM_STEP))}
              disabled={zoom >= MAX_ZOOM}
              title="Zoom in"
              className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            >
              <ZoomIn className="h-3.5 w-3.5" />
            </button>
            {zoom !== DEFAULT_ZOOM && (
              <button
                type="button"
                onClick={() => setZoom(DEFAULT_ZOOM)}
                title="Reset zoom (50%)"
                className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>

      {pageCount > 1 && (
        <div>
          <button
            type="button"
            onClick={() => setShowPages((s) => !s)}
            className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            {showPages ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
            {showPages ? "Hide pages" : `Show all ${pageCount} pages`}
          </button>

          {showPages && (
            <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
              {Array.from({ length: pageCount }, (_, i) => (
                <PageThumb
                  key={i}
                  invoiceId={invoiceId}
                  page={i}
                  active={i === currentPage}
                  onSelect={() => setCurrentPage(i)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
