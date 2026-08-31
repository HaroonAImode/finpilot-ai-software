import { useEffect, useRef, useState } from "react";
import { AlertCircle } from "lucide-react";
import { fetchInvoicePage } from "@/lib/invoice-service";

interface ActiveField {
  page: number;
  bbox: [number, number, number, number];
}

/**
 * Shows one rendered page of the document with a single highlighted box for
 * the currently hovered/focused field (Scanner v2's grounded review UI).
 * Scaling is computed live from the rendered <img>'s own clientWidth/Height
 * against pageDimensions (native units — PDF points for a pdf_text page,
 * rasterized pixels for an ocr page) — this is correct at any DPI, since
 * both the preview PNG and pageDimensions derive from the same source page.
 */
export function BoundingBoxOverlay({
  invoiceId, filename, page, pageDimensions, activeField, fields = [],
}: {
  invoiceId: string;
  filename: string;
  page: number;
  pageDimensions: [number, number] | undefined;
  activeField: ActiveField | null;
  /** Every located field on the document. Drawn faintly so the grounding is
   * visible at a glance rather than only while hovering a specific field —
   * an overlay that shows nothing until hovered reads as broken. */
  fields?: ActiveField[];
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [renderedSize, setRenderedSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setObjectUrl(null);
    setError(null);
    setRenderedSize(null);

    fetchInvoicePage(invoiceId, page)
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || "Could not load this page");
      });

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [invoiceId, page]);

  // Tracks the <img>'s live rendered size so the overlay stays aligned on
  // resize. ResizeObserver alone isn't enough here: right after objectUrl
  // changes the <img> has width (100% of its container, known immediately)
  // but no height yet (height:auto has nothing to resolve against until the
  // image decodes), so the very first measurement can race the image load
  // and get stuck reporting a 0 height forever if nothing else re-triggers
  // it — the explicit onLoad handler below is the reliable trigger.
  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const update = () => setRenderedSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [objectUrl]);

  const handleImageLoad = () => {
    const el = imgRef.current;
    if (el) setRenderedSize({ w: el.clientWidth, h: el.clientHeight });
  };

  const project = (candidate: ActiveField | null) => {
    if (!candidate || candidate.page !== page || !pageDimensions || !renderedSize) return null;
    const [pageWidth, pageHeight] = pageDimensions;
    if (!pageWidth || !pageHeight) return null;
    const scaleX = renderedSize.w / pageWidth;
    const scaleY = renderedSize.h / pageHeight;
    const [x0, y0, x1, y1] = candidate.bbox;
    return {
      left: x0 * scaleX, top: y0 * scaleY,
      width: (x1 - x0) * scaleX, height: (y1 - y0) * scaleY,
    };
  };

  const box = project(activeField);
  const faintBoxes = fields
    .map(project)
    .filter((b): b is NonNullable<typeof b> => b !== null && b.width > 0 && b.height > 0);

  return (
    <div className="relative mt-4 max-h-[70vh] overflow-auto rounded-xl border bg-muted/40">
      {error ? (
        <div className="grid aspect-[4/3] place-items-center gap-2 text-center text-sm text-muted-foreground">
          <AlertCircle className="mx-auto h-6 w-6 text-destructive" />
          <p>{error}</p>
        </div>
      ) : !objectUrl ? (
        <div className="grid aspect-[4/3] place-items-center text-sm text-muted-foreground">Loading page…</div>
      ) : (
        <div className="relative w-full">
          {/* No object-contain/fixed-aspect box here on purpose: the <img>
           * renders at its own natural aspect ratio (w-full, height auto),
           * so its clientWidth/clientHeight IS the actual rendered content
           * size the scaling math above assumes — object-contain inside a
           * differently-shaped fixed box would letterbox the image and
           * silently misalign every box against the visible page. */}
          <img ref={imgRef} src={objectUrl} alt={filename} className="block w-full" onLoad={handleImageLoad} />
          {faintBoxes.map((b, i) => (
            <div
              key={i}
              className="pointer-events-none absolute rounded-sm border border-primary/35 bg-primary/5"
              style={{ left: b.left, top: b.top, width: b.width, height: b.height }}
            />
          ))}
          {box && (
            <div
              className="pointer-events-none absolute rounded-sm border-2 border-primary bg-primary/15 transition-[left,top,width,height]"
              style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
            />
          )}
        </div>
      )}
    </div>
  );
}
