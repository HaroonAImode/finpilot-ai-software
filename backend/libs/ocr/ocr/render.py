"""Renders a single page of a document to PNG bytes — the source image the
bounding-box review UI overlays field boxes onto (docs/invoice-ocr-plan.md's
"second scanner experience"). Reuses the same fitz/_RENDER_DPI rasterization
pdf.py already relies on for its OCR fallback, so a page rendered here lines
up with the pixel-space page_dimensions ExtractionResult reports for an
"ocr"-method page; for a "pdf_text" page the caller scales by the PDF-point
page_dimensions instead, since this always rasterizes at pixel resolution
regardless of the source page's own native coordinate space.
"""
import io

import fitz  # PyMuPDF

from ocr.extract import classify_file
from ocr.image_io import decode_image
from ocr.pdf import _RENDER_DPI


class PageOutOfRange(Exception):
    """Raised when the requested page index doesn't exist in the document."""


def render_page_png(content: bytes, filename: str, mimetype: str | None, page: int) -> bytes:
    """`page` is 0-indexed, matching PositionedWord.page/FieldValue.page
    throughout the rest of the pipeline."""
    kind = classify_file(filename, mimetype)

    if kind == "pdf":
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            if page < 0 or page >= len(doc):
                raise PageOutOfRange(f"Page {page} does not exist in this {len(doc)}-page document")
            return doc[page].get_pixmap(dpi=_RENDER_DPI).tobytes("png")
        finally:
            doc.close()

    if page != 0:
        raise PageOutOfRange("An image upload is always a single page (page 0)")
    image = decode_image(content).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
