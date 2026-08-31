/**
 * Manual crop adjustment — drag the four corner handles to refine a
 * captured document's crop.
 *
 * COORDINATE SPACE — the critical invariant that was previously broken:
 *
 *   The <img> uses object-contain inside a wider container div. This means
 *   the rendered image occupies only a sub-rectangle of the container
 *   (black bars appear top/bottom for portrait photos in a landscape box).
 *
 *   Old code used containerRef.getBoundingClientRect() for pointer math
 *   and expressed all positions as %-of-container, then applyCropToImage()
 *   applied those same percentages to naturalWidth/Height — but those are
 *   different spaces. Container-% ≠ image-% when letterboxing exists, so
 *   the crop was always off.
 *
 *   Fix: ref the <img> element directly (imgRef). Every pointer event is
 *   converted relative to imgRef.getBoundingClientRect(). Overlay/handle
 *   positions are expressed in px from the measured imgRect. The box
 *   stored in state is always in "% of image pixels", which is exactly
 *   what applyCropToImage() multiplies by naturalWidth/Height.
 *
 * Re-crop UX: when initialBox is provided (user has already cropped once),
 * the CropAdjuster opens with that box pre-set. imageUrl should be the
 * *original* (pre-crop) image so the user can widen or narrow the selection.
 */
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface CropBox {
  /** All four in percent (0–100) of the image's *rendered pixel area*
   *  (same as natural pixel dimensions since object-contain preserves AR). */
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const MIN_BOX_SIZE_PCT = 5;

type Corner = "topLeft" | "topRight" | "bottomLeft" | "bottomRight";

export function CropAdjuster({
  imageUrl,
  initialBox,
  onApply,
  onCancel,
}: {
  imageUrl: string;
  initialBox?: CropBox;
  onApply: (box: CropBox) => void;
  onCancel: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const [box, setBox] = useState<CropBox>(initialBox ?? { left: 5, top: 5, right: 95, bottom: 95 });

  // The rendered image's bounding rect, relative to the container div.
  // Updated on image load and on any container size change (ResizeObserver).
  const [imgRect, setImgRect] = useState({
    offsetLeft: 0,
    offsetTop: 0,
    width: 0,
    height: 0,
  });

  const measureImg = useCallback(() => {
    const img = imgRef.current;
    const container = containerRef.current;
    if (!img || !container || img.naturalWidth === 0) return;
    const cr = container.getBoundingClientRect();
    const ir = img.getBoundingClientRect();
    setImgRect({
      offsetLeft: ir.left - cr.left,
      offsetTop: ir.top - cr.top,
      width: ir.width,
      height: ir.height,
    });
  }, []);

  // useLayoutEffect so handles appear without a one-frame flash on open.
  useLayoutEffect(() => {
    const img = imgRef.current;
    const container = containerRef.current;
    if (!img || !container) return;
    if (img.complete && img.naturalWidth > 0) measureImg();
    img.addEventListener("load", measureImg);
    const ro = new ResizeObserver(measureImg);
    ro.observe(container);
    return () => {
      img.removeEventListener("load", measureImg);
      ro.disconnect();
    };
  }, [imageUrl, measureImg]);

  const draggingCorner = useRef<Corner | null>(null);

  /** Convert pointer clientX/Y → box percentage relative to the *rendered
   *  image area* (not the outer container). Stored box% then maps 1:1 to
   *  naturalWidth/Height in applyCropToImage — no letterbox offset. */
  const clientToBoxPct = (clientX: number, clientY: number): { x: number; y: number } => {
    const img = imgRef.current;
    if (!img) return { x: 0, y: 0 };
    const ir = img.getBoundingClientRect();
    return {
      x: Math.min(100, Math.max(0, ((clientX - ir.left) / ir.width) * 100)),
      y: Math.min(100, Math.max(0, ((clientY - ir.top) / ir.height) * 100)),
    };
  };

  const handlePointerDown = (corner: Corner) => (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    draggingCorner.current = corner;
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const corner = draggingCorner.current;
    if (!corner) return;
    const { x, y } = clientToBoxPct(e.clientX, e.clientY);
    setBox((prev) => {
      const next = { ...prev };
      if (corner === "topLeft") {
        next.left = Math.min(x, prev.right - MIN_BOX_SIZE_PCT);
        next.top = Math.min(y, prev.bottom - MIN_BOX_SIZE_PCT);
      } else if (corner === "topRight") {
        next.right = Math.max(x, prev.left + MIN_BOX_SIZE_PCT);
        next.top = Math.min(y, prev.bottom - MIN_BOX_SIZE_PCT);
      } else if (corner === "bottomLeft") {
        next.left = Math.min(x, prev.right - MIN_BOX_SIZE_PCT);
        next.bottom = Math.max(y, prev.top + MIN_BOX_SIZE_PCT);
      } else {
        next.right = Math.max(x, prev.left + MIN_BOX_SIZE_PCT);
        next.bottom = Math.max(y, prev.top + MIN_BOX_SIZE_PCT);
      }
      return next;
    });
  };

  const handlePointerUp = () => {
    draggingCorner.current = null;
  };

  // Convert box-% → px offset in the container div, for CSS positioning.
  const toPx = (pct: number, axis: "x" | "y"): number =>
    axis === "x"
      ? imgRect.offsetLeft + (pct / 100) * imgRect.width
      : imgRect.offsetTop + (pct / 100) * imgRect.height;

  const leftPx = toPx(box.left, "x");
  const rightPx = toPx(box.right, "x");
  const topPx = toPx(box.top, "y");
  const bottomPx = toPx(box.bottom, "y");

  const handleStyle: React.CSSProperties = {
    position: "absolute",
    width: 24,
    height: 24,
    borderRadius: 9999,
    background: "white",
    border: "3px solid #22c55e",
    boxShadow: "0 1px 6px rgba(0,0,0,0.5)",
    touchAction: "none",
    cursor: "grab",
    zIndex: 10,
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        ref={containerRef}
        className="relative select-none overflow-hidden rounded-xl bg-black"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <img
          ref={imgRef}
          src={imageUrl}
          alt="Adjust crop"
          className="pointer-events-none block max-h-[340px] w-full object-contain"
          draggable={false}
          onLoad={measureImg}
        />

        {/* Dimmed overlay — px-based so it tracks the rendered image area,
            not the container edges (which differ when letterboxing exists). */}

        {/* Top — also covers any letterbox area above the image */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 bg-black/65"
          style={{ height: topPx }}
        />
        {/* Bottom */}
        <div
          className="pointer-events-none absolute inset-x-0 bg-black/65"
          style={{ top: bottomPx, bottom: 0 }}
        />
        {/* Left */}
        <div
          className="pointer-events-none absolute bg-black/65"
          style={{ left: 0, top: topPx, width: leftPx, height: bottomPx - topPx }}
        />
        {/* Right */}
        <div
          className="pointer-events-none absolute bg-black/65"
          style={{ left: rightPx, top: topPx, right: 0, height: bottomPx - topPx }}
        />

        {/* Crop-box border */}
        <div
          className="pointer-events-none absolute border-2 border-green-400"
          style={{ left: leftPx, top: topPx, width: rightPx - leftPx, height: bottomPx - topPx }}
        />

        {/* Corner handles */}
        <div
          style={{ ...handleStyle, left: leftPx, top: topPx, transform: "translate(-50%,-50%)" }}
          onPointerDown={handlePointerDown("topLeft")}
        />
        <div
          style={{ ...handleStyle, left: rightPx, top: topPx, transform: "translate(-50%,-50%)" }}
          onPointerDown={handlePointerDown("topRight")}
        />
        <div
          style={{ ...handleStyle, left: leftPx, top: bottomPx, transform: "translate(-50%,-50%)" }}
          onPointerDown={handlePointerDown("bottomLeft")}
        />
        <div
          style={{
            ...handleStyle,
            left: rightPx,
            top: bottomPx,
            transform: "translate(-50%,-50%)",
          }}
          onPointerDown={handlePointerDown("bottomRight")}
        />
      </div>

      <p className="text-center text-xs text-muted-foreground">
        Drag a corner handle to adjust, then tap <strong>Save Crop</strong>.
      </p>

      <div className="flex gap-2">
        <Button variant="outline" className="flex-1 gap-2 rounded-xl" onClick={onCancel}>
          <X className="h-4 w-4" /> Cancel
        </Button>
        <Button className="flex-1 gap-2 rounded-xl" onClick={() => onApply(box)}>
          <Check className="h-4 w-4" /> Save Crop
        </Button>
      </div>
    </div>
  );
}

/** Crops an image (object URL) to the given CropBox.
 *
 * The box values are percentages of the image's pixel dimensions, which is
 * exactly what the CropAdjuster now produces. No letterbox adjustment
 * needed — pointer events are already converted relative to the img element
 * itself, not its containing div. */
export async function applyCropToImage(imageUrl: string, box: CropBox): Promise<Blob> {
  const img = new Image();
  img.src = imageUrl;
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () => reject(new Error("Could not load the image to crop"));
  });

  const x0 = (box.left / 100) * img.naturalWidth;
  const y0 = (box.top / 100) * img.naturalHeight;
  const w = ((box.right - box.left) / 100) * img.naturalWidth;
  const h = ((box.bottom - box.top) / 100) * img.naturalHeight;

  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w));
  canvas.height = Math.max(1, Math.round(h));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Could not create a canvas to crop this image");
  ctx.drawImage(img, x0, y0, w, h, 0, 0, canvas.width, canvas.height);

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Could not export the cropped image"))),
      "image/jpeg",
      0.92,
    );
  });
}
