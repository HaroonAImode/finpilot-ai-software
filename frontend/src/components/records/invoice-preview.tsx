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
import { useEffect, useRef, useState, type SyntheticEvent } from "react";
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

/** A *ratio* fallback (of the frame's own height) only used before the
 *  image's real dimensions are known — see `fitZoom` below for what
 *  actually decides the zoom once it loads. Was a flat `0.5` before: every
 *  document, whatever its actual pixel size, opened at exactly half of
 *  whatever "contained" size happened to fall out of `object-contain` —
 *  which is why some receipts opened looking tiny inside a large frame.
 *  `fitZoom` replaces that with a real computation once the image loads. */
const DEFAULT_ZOOM = 1;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;
/** The frame itself never resizes — same footprint at any zoom level. It
 *  used to be a `min-h-[420px]` with no matching max, so zooming in grew
 *  the whole box (and everything below it on the page) taller instead of
 *  scrolling within a fixed frame, exactly the "the whole preview gets
 *  bigger/smaller" report. A real, bounded frame is what makes
 *  `overflow-auto` on it actually do anything.
 *
 *  720px is the standalone (non-`fill`) size — a real, page-sized viewing
 *  area closer to how a printed A4 sheet reads on screen. In `fill` mode
 *  (the Saved Records 3-column layout, where this pane owns its own grid
 *  column) the frame instead stretches to `h-full` of that column. */
const FRAME_HEIGHT_PX = 720;

function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, +z.toFixed(2)));
}

/** The zoom that makes the image fill its frame as large as possible
 *  without cropping — "large as of its own size," not an arbitrary
 *  fraction. `object-contain` already fits the image to the frame at 100%
 *  scale, so 1.0 is normally correct; this only scales *up* past 100% for
 *  a document whose rendered pixels are smaller than the frame (a
 *  low-resolution scan), so it still reads as a real, filled page instead
 *  of a small image floating in empty space — and never scales past
 *  MAX_ZOOM, so a huge source image doesn't jump to an absurd size. */
function fitZoom(naturalW: number, naturalH: number, frameW: number, frameH: number): number {
  if (naturalW <= 0 || naturalH <= 0 || frameW <= 0 || frameH <= 0) return DEFAULT_ZOOM;
  const containedScale = Math.min(frameW / naturalW, frameH / naturalH, 1);
  const upscaleToFillFrame = Math.min(frameW / naturalW, frameH / naturalH);
  return clampZoom(containedScale < 1 ? 1 : upscaleToFillFrame);
}

function touchDistance(a: Touch, b: Touch): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

function touchMidpoint(a: Touch, b: Touch): { x: number; y: number } {
  return { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 };
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
  fill = false,
}: {
  invoiceId: string;
  filename: string | null;
  /** Total pages in the source document — Invoice.page_dimensions.length.
   *  Omitted (or 1) simply means no page picker renders; the single-page
   *  preview below always works regardless. */
  pageCount?: number;
  /** Stretch to the height of its parent instead of the standalone
   *  `FRAME_HEIGHT_PX` — for the Saved Records 3-column layout, where this
   *  pane owns a whole sticky grid column rather than sitting above a
   *  stack of other content. */
  fill?: boolean;
}) {
  const [currentPage, setCurrentPage] = useState(0);
  const [showPages, setShowPages] = useState(false);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  // The zoom that fills the frame with this specific document, computed
  // once its real pixel size is known (see `handleImageLoad` below) — this
  // is what "reset zoom" returns to, not a flat guess, and what a fresh
  // page opens at instead of the old flat 50%.
  const [fitZoomValue, setFitZoomValue] = useState(DEFAULT_ZOOM);
  // How far the image is dragged off its centered position, in on-screen
  // pixels — applied as a `translate()` alongside `scale(zoom)` on the same
  // element. Deliberately NOT the frame's native `scrollLeft`/`scrollTop`:
  // a `transform: scale()`'d flex child does not reliably grow its parent's
  // *scrollable* overflow in every browser (confirmed live — the earlier
  // scroll-based version visibly did nothing once zoomed in, even though
  // the image was genuinely larger than the frame). A manual pan offset
  // has no such dependency: it just moves the element, unconditionally.
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const frameRef = useRef<HTMLDivElement>(null);
  // Two active touches' starting distance + the zoom/pan at that moment, so
  // a pinch's midpoint delta maps to a zoom *ratio* rather than jumping by
  // a fixed step per event — same reason a real image viewer's pinch feels
  // smooth instead of stepping in 25% jumps like the buttons do. `midpoint`
  // lets the same two-finger gesture pan at the same time it zooms — a real
  // pinch is never purely "in place," the midpoint always drifts a little.
  const pinchRef = useRef<{
    distance: number;
    zoom: number;
    midpoint: { x: number; y: number };
    pan: { x: number; y: number };
  } | null>(null);
  // Click-and-drag panning for a mouse/trackpad — Pointer Events (not
  // separate mouse/touch handlers) since a single pointer down/move/up
  // sequence is the same gesture whether it comes from a mouse or one
  // finger on a touchscreen.
  const dragRef = useRef<{ x: number; y: number; pan: { x: number; y: number } } | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  /** Keeps `pan` from drifting the image completely out of the frame —
   *  generous (this is "don't lose the document," not a tight bound), and
   *  scales with zoom so more zoom allows more room to pan around in. */
  function clampPan(p: { x: number; y: number }, z: number, frame: HTMLDivElement) {
    const maxX = (frame.clientWidth * z) / 2;
    const maxY = (frame.clientHeight * z) / 2;
    return { x: Math.min(maxX, Math.max(-maxX, p.x)), y: Math.min(maxY, Math.max(-maxY, p.y)) };
  }

  /** Computed on the actual `<img>`'s `onLoad` — only then are its real
   *  `naturalWidth/naturalHeight` known, which `fitZoom` needs alongside
   *  the frame's own current pixel size to decide how large this specific
   *  document should open. */
  function handleImageLoad(e: SyntheticEvent<HTMLImageElement>) {
    const frame = frameRef.current;
    const img = e.currentTarget;
    if (!frame) return;
    const fit = fitZoom(
      img.naturalWidth,
      img.naturalHeight,
      frame.clientWidth - 32,
      frame.clientHeight - 24,
    );
    setFitZoomValue(fit);
    setZoom(fit);
    setPan({ x: 0, y: 0 });
  }

  // Native, non-passive listeners — see this file's module docstring for
  // why a React onWheel/onTouchMove prop can't do this (they're passive by
  // default, so preventDefault() on them is silently ignored and the
  // browser's own page-zoom fires anyway).
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;

    function handleWheel(e: WheelEvent) {
      e.preventDefault();
      if (e.ctrlKey) {
        // Pinch-as-wheel (trackpad) or an actual held-Ctrl+scroll — zoom,
        // anchored on the frame's own center (see the transform-origin
        // comment on the image below), not the cursor.
        setZoom((z) => clampZoom(z - e.deltaY * 0.01));
        return;
      }
      // Plain wheel/trackpad scroll now PANS instead of doing nothing —
      // this frame is no longer a native scroll container (see `pan`'s own
      // comment for why), so without this a two-finger trackpad scroll had
      // no effect at all once zoomed in.
      setPan((p) => {
        const frame = frameRef.current;
        if (!frame) return p;
        return clampPan({ x: p.x - e.deltaX, y: p.y - e.deltaY }, zoom, frame);
      });
    }

    function handleTouchStart(e: TouchEvent) {
      if (e.touches.length === 2) {
        pinchRef.current = {
          // Just checked `.length === 2` above — both indices exist.
          distance: touchDistance(e.touches[0]!, e.touches[1]!),
          zoom,
          midpoint: touchMidpoint(e.touches[0]!, e.touches[1]!),
          pan,
        };
      } else if (e.touches.length === 1) {
        // A single finger pans too — same gesture as a mouse drag, just
        // via touch. `touchAction: none` on the frame (see below) hands
        // this entirely to us instead of the browser's own native scroll.
        dragRef.current = { x: e.touches[0]!.clientX, y: e.touches[0]!.clientY, pan };
        setIsPanning(true);
      }
    }
    function handleTouchMove(e: TouchEvent) {
      const frame = frameRef.current;
      if (!frame) return;
      if (e.touches.length === 2 && pinchRef.current) {
        e.preventDefault();
        // Just checked `.length === 2` above — both indices exist.
        const distance = touchDistance(e.touches[0]!, e.touches[1]!);
        const nextZoom = clampZoom(pinchRef.current.zoom * (distance / pinchRef.current.distance));
        setZoom(nextZoom);

        // The pan half of the same gesture — fingers drifting together
        // (not apart/together, which is the zoom above) moves the visible
        // area the same amount, in the same direction they moved.
        const midpoint = touchMidpoint(e.touches[0]!, e.touches[1]!);
        setPan(
          clampPan(
            {
              x: pinchRef.current.pan.x + (midpoint.x - pinchRef.current.midpoint.x),
              y: pinchRef.current.pan.y + (midpoint.y - pinchRef.current.midpoint.y),
            },
            nextZoom,
            frame,
          ),
        );
      } else if (e.touches.length === 1 && dragRef.current) {
        e.preventDefault();
        const touch = e.touches[0]!;
        setPan(
          clampPan(
            {
              x: dragRef.current.pan.x + (touch.clientX - dragRef.current.x),
              y: dragRef.current.pan.y + (touch.clientY - dragRef.current.y),
            },
            zoom,
            frame,
          ),
        );
      }
    }
    function handleTouchEnd(e: TouchEvent) {
      if (e.touches.length < 2) pinchRef.current = null;
      if (e.touches.length < 1) {
        dragRef.current = null;
        setIsPanning(false);
      }
    }

    // Click-and-drag panning for a mouse/trackpad — the touch path above
    // only ever covers touchscreens; a zoomed-in document otherwise had no
    // pan affordance at all for a mouse user. `mousemove`/`mouseup` are on
    // `document`, not the frame, so the drag keeps tracking even if the
    // cursor slips outside the frame's own bounds mid-drag — the standard
    // pattern for any click-and-drag interaction.
    function handleMouseDown(e: MouseEvent) {
      // Only the plain left button, and not on a control sitting inside the
      // frame (the zoom buttons, a page thumbnail) — those need their own
      // click to register, not be swallowed by a drag start.
      if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
      dragRef.current = { x: e.clientX, y: e.clientY, pan };
      setIsPanning(true);
    }
    function handleMouseMove(e: MouseEvent) {
      const frame = frameRef.current;
      if (!dragRef.current || !frame) return;
      e.preventDefault();
      setPan(
        clampPan(
          {
            x: dragRef.current.pan.x + (e.clientX - dragRef.current.x),
            y: dragRef.current.pan.y + (e.clientY - dragRef.current.y),
          },
          zoom,
          frame,
        ),
      );
    }
    function handleMouseUp() {
      if (!dragRef.current) return;
      dragRef.current = null;
      setIsPanning(false);
    }

    frame.addEventListener("wheel", handleWheel, { passive: false });
    frame.addEventListener("touchstart", handleTouchStart, { passive: true });
    frame.addEventListener("touchmove", handleTouchMove, { passive: false });
    frame.addEventListener("touchend", handleTouchEnd, { passive: true });
    frame.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      frame.removeEventListener("wheel", handleWheel);
      frame.removeEventListener("touchstart", handleTouchStart);
      frame.removeEventListener("touchmove", handleTouchMove);
      frame.removeEventListener("touchend", handleTouchEnd);
      frame.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
    // `zoom`/`pan` are read fresh into pinchRef/dragRef only at gesture
    // start, not on every frame — re-subscribing whenever either changes
    // would reset an in-progress gesture's baseline. Each handler's closure
    // over them is refreshed by this effect re-running after every change
    // anyway (buttons or gestures), which is exactly the up-to-date value a
    // *new* gesture needs.
  }, [zoom, pan]);

  const pageQuery = useQuery({
    queryKey: ["invoice-preview-page", invoiceId, currentPage],
    queryFn: () => fetchInvoicePage(invoiceId, currentPage),
    staleTime: Infinity,
    retry: false,
  });
  const objectUrl = useObjectUrl(pageQuery.data);

  return (
    <div className={fill ? "flex h-full flex-col gap-2" : "flex flex-col gap-2"}>
      <div
        ref={frameRef}
        // `touchAction: none` — every gesture in this frame (one-finger
        // pan, two-finger pinch/pan) is handled entirely by our own JS
        // handlers now (see the effect above), not native browser
        // scroll/zoom, so the browser must not intercept any of them.
        // `overflow: hidden`, not `auto`: this frame is no longer a native
        // scroll container at all — panning moves the image itself via a
        // `translate()` (see `pan` state's own comment for why the earlier
        // `scrollLeft`/`scrollTop` approach silently did nothing).
        style={{
          height: fill ? "100%" : `${FRAME_HEIGHT_PX}px`,
          touchAction: "none",
          cursor: isPanning ? "grabbing" : objectUrl ? "grab" : "default",
        }}
        className="relative min-h-0 flex-1 overflow-hidden rounded-xl border bg-muted/30 select-none"
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
          <div className="flex h-full items-start justify-center p-4 pt-2">
            {/* Top-aligned and horizontally centered so the receipt starts
             *  directly near the top of the frame. `translate()` (the `pan`
             *  offset) is applied before `scale()` so panning always moves
             *  the image by real screen pixels regardless of zoom level —
             *  the reverse order would make the pan distance itself scale
             *  with zoom, which reads as pan "speeding up" the more zoomed
             *  in you are. transformOrigin: top center keeps the top of the
             *  document anchored in place as the zoom level itself changes. */}
            <img
              src={objectUrl}
              alt={filename ?? "Invoice document"}
              onLoad={handleImageLoad}
              draggable={false}
              style={{
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                transformOrigin: "top center",
              }}
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
            {(Math.abs(zoom - fitZoomValue) > 0.01 || pan.x !== 0 || pan.y !== 0) && (
              <button
                type="button"
                onClick={() => {
                  setZoom(fitZoomValue);
                  setPan({ x: 0, y: 0 });
                }}
                title={`Reset view (${Math.round(fitZoomValue * 100)}%)`}
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
