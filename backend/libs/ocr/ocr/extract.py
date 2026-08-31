"""Entry point: classify the file, route to the right extraction path.
See docs/invoice-ocr-plan.md §2 for the routing table this implements.
"""
import logging
from pathlib import Path
from typing import Literal, Optional

from ocr.image import ocr_image_words, words_to_readable_text
from ocr.image_io import decode_image
from ocr.models import ExtractionResult, UnsupportedFileType
from ocr.pdf import extract_from_pdf

logger = logging.getLogger(__name__)

_PDF_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}


def classify_file(filename: str, mimetype: Optional[str]) -> Literal["pdf", "image"]:
    ext = Path(filename).suffix.lower()
    if mimetype == "application/pdf" or ext in _PDF_EXTENSIONS:
        return "pdf"
    if (mimetype is not None and mimetype.startswith("image/")) or ext in _IMAGE_EXTENSIONS:
        return "image"
    raise UnsupportedFileType(
        f"Cannot extract text from '{filename}' (mimetype={mimetype!r}) — expected a PDF or image"
    )


def extract_text(content: bytes, filename: str, mimetype: Optional[str] = None) -> ExtractionResult:
    """Stage 1 of docs/invoice-ocr-plan.md's pipeline. `content` is the raw
    file bytes exactly as uploaded/synced in — the caller does not need to
    know or care whether this ends up going through PyMuPDF's text layer or
    Tesseract; that decision is made here from the file's own shape.
    """
    kind = classify_file(filename, mimetype)
    if kind == "pdf":
        return extract_from_pdf(content)

    image = decode_image(content)
    words = ocr_image_words(image, page=0)
    confidence = (sum(w.confidence for w in words) / len(words)) if words else 0.0
    return ExtractionResult(
        text=words_to_readable_text(words), confidence=confidence, method="ocr", pages=1,
        words_by_page=[words], page_dimensions=[(float(image.width), float(image.height))],
    )


def extract_text_with_engine(
    content: bytes, filename: str, mimetype: Optional[str] = None, *,
    engine: Literal["liteparse", "tesseract"] = "liteparse",
    paddleocr_url: Optional[str] = None, ocr_language: str = "en",
) -> ExtractionResult:
    """Top-level Stage 1 entry point with engine selection — the
    LiteParse+PaddleOCR migration's rollback switch
    (docs/invoice-ocr-plan.md's migration section). `engine="tesseract"` is
    exactly `extract_text()` above, completely unmodified — a genuine
    independent code path, not a shared implementation with a flag, so a
    rollback is never at risk of inheriting a liteparse-path bug.
    `engine="liteparse"` (the default) tries LiteParse (which itself tries
    the configured PaddleOCR service, then its own built-in OCR — see
    ocr.liteparse's docstring); if LiteParse fails outright (both of its
    own paths exhausted, e.g. a file it genuinely cannot parse), this falls
    back one more level to the legacy pipeline rather than erroring — the
    same fallback discipline as the rest of this project's engine.
    """
    if engine == "tesseract":
        return extract_text(content, filename, mimetype)

    from ocr.liteparse import LiteParseOcrError
    from ocr.liteparse import extract_text as extract_text_liteparse

    try:
        return extract_text_liteparse(
            content, filename, mimetype, paddleocr_url=paddleocr_url, ocr_language=ocr_language,
        )
    except LiteParseOcrError as exc:
        logger.warning("LiteParse path failed entirely (%s) — falling back to the legacy Tesseract pipeline", exc)
        return extract_text(content, filename, mimetype)


def extract_documents_with_engine(
    content: bytes, filename: str, mimetype: Optional[str] = None, *,
    engine: Literal["liteparse", "tesseract"] = "liteparse",
    paddleocr_url: Optional[str] = None, ocr_language: str = "en",
) -> list[tuple[ExtractionResult, bytes]]:
    """Document Preprocessing (docs/invoice-ocr-plan.md §7) + Stage 1,
    multi-document-aware. Returns one `(ExtractionResult, image_bytes)` pair
    per document this upload actually contains — length 1 for the
    overwhelming common case (whether or not a background-removing crop
    happened), length 2+ only when `ocr.preprocess` confidently found more
    than one separate document in a single photo.

    PDF inputs are not preprocessed at all — a PDF page is already a
    single, clean, born-digital document; the excess-background/multiple-
    documents-in-one-shot problem this stage exists for is specific to a
    phone-camera photo. `extract_text_with_engine` above is completely
    unmodified and untouched by this function's existence — any caller
    that only ever wants today's single-document behavior keeps getting
    exactly that, unchanged.
    """
    kind = classify_file(filename, mimetype)
    if kind == "pdf":
        result = extract_text_with_engine(
            content, filename, mimetype, engine=engine, paddleocr_url=paddleocr_url, ocr_language=ocr_language,
        )
        return [(result, content)]

    from ocr.preprocess import preprocess_image

    try:
        crops = [document.image_bytes for document in preprocess_image(content)]
    except ValueError as exc:
        # Bytes that classify_file already accepted as "an image" but that
        # OpenCV/Pillow still cannot decode (a genuinely corrupt upload) —
        # the same conservative rule as everywhere else in this module:
        # fall back to the original bytes rather than fail the whole scan
        # over a preprocessing-only concern.
        logger.warning("Document preprocessing could not decode this image (%s) — using it unprocessed", exc)
        crops = [content]

    return [
        (
            extract_text_with_engine(
                image_bytes, filename, mimetype, engine=engine,
                paddleocr_url=paddleocr_url, ocr_language=ocr_language,
            ),
            image_bytes,
        )
        for image_bytes in crops
    ]
