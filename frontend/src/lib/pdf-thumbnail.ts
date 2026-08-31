/**
 * Renders a PDF's first page to a small raster thumbnail entirely in the
 * browser — no backend rasterization endpoint needed for Slack/Email/
 * Browser-Upload documents the way Invoice Service's AI-Engine-backed
 * `/page/{n}` exists for invoices. Those three connectors have no such
 * endpoint (see source-column.tsx's own scope notes), and building one in
 * three more backend services just to draw a small icon would be a lot of
 * new surface for something the browser can already do on the bytes it
 * already fetched.
 */
import * as pdfjsLib from "pdfjs-dist";
// Vite's `?url` import gives the built worker file's final URL rather than
// its source — pdf.js insists on a real Worker script, not the bundler's
// own module graph, so this has to be a URL pdfjsLib can hand to `new
// Worker(...)` itself.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const THUMBNAIL_WIDTH_PX = 200;

/** Returns a PNG data URL of the PDF's first page, or null if the bytes do
 *  not parse as a PDF at all (never throws — a thumbnail is a nice-to-have,
 *  not something worth surfacing an error banner over). */
export async function renderPdfFirstPageThumbnail(blob: Blob): Promise<string | null> {
  try {
    const buffer = await blob.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: buffer }).promise;
    const page = await pdf.getPage(1);

    const unscaledViewport = page.getViewport({ scale: 1 });
    const scale = THUMBNAIL_WIDTH_PX / unscaledViewport.width;
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const context = canvas.getContext("2d");
    if (!context) return null;

    await page.render({ canvas, canvasContext: context, viewport }).promise;
    return canvas.toDataURL("image/png");
  } catch {
    return null;
  }
}
