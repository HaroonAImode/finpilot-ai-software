"""PDF text extraction — the fast, exact path for anything with a real text
layer (any vendor-generated/exported invoice, and anything Email Connector
syncs in), with a fallback to page-rasterize-then-OCR for a PDF that is
actually a scanned image with no text layer at all. See
docs/invoice-ocr-plan.md §2.
"""
import io

import fitz  # PyMuPDF
from PIL import Image

from ocr.image import ocr_image_words, words_to_readable_text
from ocr.models import ExtractionResult, PositionedWord

# A page with a real text layer returns hundreds of characters for anything
# invoice-shaped; a scanned page rendered to PDF returns ~0. This is a cheap,
# reliable heuristic — no page-layout analysis needed to tell the two apart.
MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 20

# 300 DPI is the standard "good enough for OCR" render resolution — higher
# costs more time/memory for accuracy gains Tesseract mostly can't use;
# lower starts losing small print (tax rates, NTN numbers) that matters here.
_RENDER_DPI = 300


def _native_page_words(page, page_index: int) -> list[PositionedWord]:
    """Word-level bounding boxes straight from PyMuPDF's own tokenizer — no
    approximation needed here, unlike the OCR path, since this is a direct
    parse of the PDF's content stream, not probabilistic recognition.

    Font size is not attached per word here (PyMuPDF's word-level API
    doesn't expose it; only span-level "dict" mode does, and spans don't map
    cleanly 1:1 to words) — Rule 3.4's vendor-name heuristic uses page
    position (top-of-document) instead in this phase; font-size as a second
    signal is a documented possible enhancement, not required for what
    Phase 2a implements.
    """
    return [
        PositionedWord(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], page=page_index, confidence=1.0)
        for w in page.get_text("words")
    ]


def extract_from_pdf(content: bytes) -> ExtractionResult:
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        page_texts = [page.get_text() for page in doc]
        total_chars = sum(len(t.strip()) for t in page_texts)
        avg_chars_per_page = total_chars / max(len(doc), 1)

        if avg_chars_per_page >= MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER:
            words_by_page = [_native_page_words(page, i) for i, page in enumerate(doc)]
            page_dimensions = [(page.rect.width, page.rect.height) for page in doc]
            return ExtractionResult(
                text="\n\n".join(page_texts), confidence=1.0, method="pdf_text", pages=len(doc),
                words_by_page=words_by_page, page_dimensions=page_dimensions,
            )

        # No real text layer — this PDF is a scanned image. Rasterize each
        # page and run it through the same OCR path a plain image upload
        # would take.
        ocr_texts = []
        confidences = []
        words_by_page = []
        page_dimensions = []
        for i, page in enumerate(doc):
            pixmap = page.get_pixmap(dpi=_RENDER_DPI)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            page_dimensions.append((float(image.width), float(image.height)))
            page_words = ocr_image_words(image, page=i)
            words_by_page.append(page_words)
            ocr_texts.append(words_to_readable_text(page_words))
            if page_words:
                confidences.append(sum(w.confidence for w in page_words) / len(page_words))

        return ExtractionResult(
            text="\n\n".join(ocr_texts),
            confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
            method="ocr",
            pages=len(doc),
            words_by_page=words_by_page,
            page_dimensions=page_dimensions,
        )
    finally:
        doc.close()
