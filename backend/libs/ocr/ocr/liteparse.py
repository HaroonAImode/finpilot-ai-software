"""LiteParse-based extraction — the new primary Stage 1 engine as of the
LiteParse+PaddleOCR migration (docs/invoice-ocr-plan.md's migration
section). Maps `liteparse`'s output onto the existing
ExtractionResult/PositionedWord contract unchanged, so Stage 2
(invoice_extraction) needs no changes at all — this module is purely an
alternative way of producing the same shape `ocr.extract.extract_text`
already does.

Deliberately NOT using LiteParse's own `complexity.needs_ocr` signal to
decide whether OCR is needed — confirmed via live testing against this
project's own real, clean, already-correctly-extracted sample invoice that
it misfires (flagged "sparse-text" and triggered an 18-second unnecessary
OCR pass on a document with a perfectly good text layer, because invoices
are visually sparse by nature: lots of whitespace relative to page area,
which is exactly what that heuristic penalizes). Instead this reuses the
same avg-chars-per-page heuristic `ocr/pdf.py`'s PyMuPDF path already relies
on and this project has already tested against real documents.
"""
import logging
from typing import Optional

import liteparse

from ocr.extract import classify_file
from ocr.models import ExtractionResult, PositionedWord

logger = logging.getLogger(__name__)

# Same reasoning/value as ocr/pdf.py's MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER —
# kept as its own constant (not imported from pdf.py) so this module stays
# independently swappable/removable during the migration without coupling
# to the legacy module it may eventually replace.
_MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER = 20


class LiteParseOcrError(Exception):
    """Raised when neither the configured OCR server (PaddleOCR) nor
    LiteParse's own built-in fallback OCR could produce a result. The
    caller (ai-engine's route) catches this and falls back to the fully
    legacy PyMuPDF+pytesseract pipeline (ocr.extract.extract_text) —
    the OCR_ENGINE=tesseract rollback path, never a hard failure."""


def _split_glued_text_item(item, page_index: int, confidence: float) -> list[PositionedWord]:
    """Splits a single TextItem's `.text` on whitespace into one
    PositionedWord per real word, distributing its bbox width across the
    sub-words proportionally to character count (a geometric
    approximation, not exact per-glyph measurement, but good enough for
    this pipeline's word-position matching — the same tolerance-based
    spirit as fields.py's own column-gap heuristics).

    Confirmed live against a real invoice this is necessary, not just
    defensive: the OCR path's own `.words` breakdown (see _flatten_words'
    docstring) is sometimes empty even when `item.text` itself contains
    multiple space-separated words (e.g. a single detected text region
    read as "ORDER DATE" with no `.words` split) — silently treating that
    whole item as one PositionedWord broke every downstream consumer that
    assumes one word per PositionedWord (fields.py's label matching in
    particular: it looks for a standalone "date" token and never finds
    one glued inside "order date").
    """
    tokens = item.text.split()
    if len(tokens) <= 1:
        return [
            PositionedWord(
                text=item.text, x0=item.x, y0=item.y,
                x1=item.x + item.width, y1=item.y + item.height,
                page=page_index, confidence=confidence,
            )
        ]
    total_chars = sum(len(t) for t in tokens)
    words: list[PositionedWord] = []
    cursor = item.x
    for token in tokens:
        share = len(token) / total_chars
        token_width = item.width * share
        words.append(
            PositionedWord(
                text=token, x0=cursor, y0=item.y,
                x1=cursor + token_width, y1=item.y + item.height,
                page=page_index, confidence=confidence,
            )
        )
        cursor += token_width
    return words


def _flatten_words(pages) -> list[list[PositionedWord]]:
    """Flattens LiteParse's TextItem/WordBox nesting into the flat
    per-page word lists the rest of this pipeline expects.

    Confirmed live that the two extraction paths shape TextItem
    differently: the native-PDF-text path produces span-level TextItems
    (e.g. one whole "TAX INVOICE" item) with a populated `.words` list
    breaking it into individual WordBoxes; the OCR path instead
    *usually* produces one word-level TextItem per detected word
    directly, with `.words` left empty — but not always (see
    _split_glued_text_item's own docstring for a real, live-found
    counterexample). Handling both: use `.words` when populated,
    otherwise split the TextItem's own text on whitespace so every
    PositionedWord this function emits is genuinely one word, never a
    multi-word glued span. A WordBox carries no confidence of its own
    (only its parent TextItem does), so a word sourced from `.words`
    inherits its span's confidence.
    """
    words_by_page: list[list[PositionedWord]] = []
    for page in pages:
        page_index = page.page_num - 1  # LiteParse pages are 1-indexed; this project's convention is 0-indexed.
        page_words: list[PositionedWord] = []
        for item in page.text_items:
            confidence = item.confidence if item.confidence is not None else 1.0
            if item.words:
                for word in item.words:
                    page_words.append(
                        PositionedWord(
                            text=word.text, x0=word.x, y0=word.y,
                            x1=word.x + word.width, y1=word.y + word.height,
                            page=page_index, confidence=confidence,
                        )
                    )
            elif item.text.strip():
                page_words.extend(_split_glued_text_item(item, page_index, confidence))
        words_by_page.append(page_words)
    return words_by_page


def _page_dimensions(pages) -> list[tuple[float, float]]:
    # Deliberately LiteParse's own reported width/height, not a
    # PIL-derived native-pixel size — LiteParse's internal coordinate
    # space (see module docstring's live-verified finding) is a scaled
    # "point" unit, not raw pixels, for an image upload, and every word's
    # bbox above is reported in that SAME unit. page_dimensions must stay
    # in whatever unit the bboxes are actually in, or the bounding-box
    # review UI's scaling math (Scanner v2) silently misaligns.
    return [(page.width, page.height) for page in pages]


def _result_confidence(words_by_page: list[list[PositionedWord]]) -> float:
    all_words = [w for page_words in words_by_page for w in page_words]
    if not all_words:
        return 0.0
    return sum(w.confidence for w in all_words) / len(all_words)


def _build_result(parsed, method: str) -> ExtractionResult:
    words_by_page = _flatten_words(parsed.pages)
    return ExtractionResult(
        text=parsed.text, confidence=_result_confidence(words_by_page), method=method,
        pages=parsed.total_pages, words_by_page=words_by_page,
        page_dimensions=_page_dimensions(parsed.pages),
    )


def extract_text(
    content: bytes, filename: str = "upload", mimetype: Optional[str] = None, *,
    paddleocr_url: Optional[str] = None, ocr_language: str = "en",
) -> ExtractionResult:
    """Stage 1, LiteParse path. `content` is the raw file bytes exactly as
    uploaded — LiteParse itself auto-detects PDF vs image from the bytes
    (confirmed live: `.parse(content)` works identically for both) and
    would in fact accept several other formats LiteParse supports natively
    (Office/OpenDocument formats) that this project's upload contract never
    has. `classify_file` is still called first — not for routing, but to
    preserve the exact existing API contract (a non-PDF/image upload must
    still be a clean 400 UnsupportedFileType, not a 502 from a LiteParse
    ParseError it wasn't written to expect — a real regression caught live
    by this project's own existing test suite during the migration).

    Routing: parses once with OCR disabled to get native text fast
    (~50ms on a real invoice, confirmed live) and applies this project's
    own proven text-layer-sufficiency check; only re-parses with OCR
    enabled when that check says the native text isn't enough (always the
    case for a plain image upload, which has no native text layer at all).
    """
    classify_file(filename, mimetype)  # raises UnsupportedFileType for anything outside PDF/image

    fast_parser = liteparse.LiteParse(ocr_enabled=False, emit_word_boxes=True)
    native_result = fast_parser.parse(content)

    avg_chars_per_page = len(native_result.text.strip()) / max(native_result.total_pages, 1)
    if avg_chars_per_page >= _MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER:
        return _build_result(native_result, method="pdf_text")

    return _extract_with_ocr(content, paddleocr_url=paddleocr_url, ocr_language=ocr_language)


def _extract_with_ocr(content: bytes, *, paddleocr_url: Optional[str], ocr_language: str) -> ExtractionResult:
    if paddleocr_url:
        # `paddleocr_url` is the *service's base URL* (e.g.
        # "http://paddleocr:8008", matching every other internal service
        # URL convention in this codebase — see ai_engine_client.py).
        # LiteParse's own ocr_server_url wants the *complete* endpoint to
        # POST to, not a base it appends its own path to — confirmed live
        # (a bare base URL produced "POST / 404 Not Found" against the
        # paddleocr service, since LiteParse posted straight to it with no
        # path appended at all) — so the "/ocr" route from
        # OCR_API_SPEC.md is appended here, once, in the one place that
        # needs to know it.
        ocr_endpoint = f"{paddleocr_url.rstrip('/')}/ocr"
        try:
            ocr_parser = liteparse.LiteParse(
                ocr_enabled=True, ocr_server_url=ocr_endpoint, ocr_language=ocr_language,
                emit_word_boxes=True, ocr_failure_fatal=True,
                # Disables LiteParse's own request-hedging (firing extra
                # redundant OCR calls after a delay if the first hasn't
                # returned yet). Confirmed live this multiplies cost badly
                # against a consistently-slow-but-working OCR server rather
                # than helping — a single real request took ~50-70s under
                # the (required, see engine.py) mkldnn-disabled config, and
                # hedging turned that into 5 duplicate calls and several
                # minutes total. Hedging is meant for flaky services, not
                # ones that reliably respond, just not instantly.
                ocr_hedge_delays_ms=[],
            )
            result = ocr_parser.parse(content)
            return _build_result(result, method="ocr")
        except Exception as exc:
            # PaddleOCR service down/timed out/erroring — fall through to
            # LiteParse's own built-in OCR (confirmed live: Tesseract-based
            # under the hood) rather than failing the whole request. The
            # caller has its own further-outer fallback to the fully
            # legacy pipeline if even this doesn't work.
            logger.warning("PaddleOCR server unavailable, falling back to LiteParse's built-in OCR: %s", exc)

    try:
        builtin_parser = liteparse.LiteParse(ocr_enabled=True, ocr_language=ocr_language, emit_word_boxes=True)
        result = builtin_parser.parse(content)
        return _build_result(result, method="ocr")
    except Exception as exc:
        raise LiteParseOcrError(f"LiteParse could not extract text via any OCR path: {exc}") from exc
