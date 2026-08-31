from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PositionedWord:
    """One word with its location on the page it came from — the raw
    material Rule 4.2 (docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md)
    needs for row/column table reconstruction, and Rule 3.4 needs for
    font-size-based vendor-name detection. Coordinates are in whatever space
    the source produced them in — PDF points for a native text layer,
    rendered-image pixels for anything that went through Tesseract — and are
    only ever compared *within* the same page, never across extraction
    methods, so the two units never need reconciling.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    #: Per-word OCR confidence (0.0-1.0). 1.0 for native PDF text (exact,
    #: not a guess — see ExtractionResult.confidence's own docstring for the
    #: same distinction).
    confidence: float
    #: Font size in points — only ever populated for a native PDF text
    #: layer (PyMuPDF exposes it; Tesseract has no equivalent concept for a
    #: rasterized image). None for anything OCR'd.
    font_size: float | None = None


@dataclass
class ExtractionResult:
    """The output of Stage 1 (docs/invoice-ocr-plan.md §2) — plain text plus
    enough about how it was obtained for the caller to weigh it into a
    combined confidence score (§3) rather than trusting a single number.
    """

    text: str
    #: 1.0 for a native PDF text layer (exact, not a guess). Tesseract's
    #: mean per-word confidence (0.0-1.0) for anything that went through OCR.
    confidence: float
    method: Literal["pdf_text", "ocr"]
    pages: int
    #: Positioned words, one list per page, in reading order within each
    #: page. Empty pages are still represented as an empty list, so
    #: `len(words_by_page) == pages` always holds.
    words_by_page: list[list[PositionedWord]] = field(default_factory=list)
    #: (width, height) per page, aligned 1:1 with words_by_page/pages — PDF
    #: points for a "pdf_text" page (page.rect.width/height), rasterized
    #: pixel dimensions for an "ocr" page. Lets a caller scale a field's
    #: bbox onto a rendered preview image regardless of what DPI that
    #: render used, since both derive from the same page.
    page_dimensions: list[tuple[float, float]] = field(default_factory=list)


class UnsupportedFileType(Exception):
    """Raised for anything that isn't a PDF or a common image format —
    callers should surface this as a clear rejection, not attempt OCR on
    it and get garbage back."""
