"""ocr.liteparse — the new primary Stage 1 engine (LiteParse+PaddleOCR
migration, docs/invoice-ocr-plan.md). Same synthetic-document conventions
as test_extract.py; the OCR path additionally needs a real Tesseract binary
on PATH, since LiteParse's own built-in OCR fallback (used here whenever no
paddleocr_url is configured) is Tesseract-based under the hood — confirmed
live during the migration, not assumed.
"""
import io
import shutil

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from ocr.extract import extract_text, extract_text_with_engine
from ocr.liteparse import LiteParseOcrError
from ocr.liteparse import extract_text as extract_text_liteparse

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
requires_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE,
    reason="tesseract binary not on PATH — LiteParse's own built-in OCR fallback needs it "
    "(see docs/invoice-ocr-plan.md §4); the ai-engine Docker image installs it via apt.",
)


def _native_text_pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 20
    content = doc.tobytes()
    doc.close()
    return content


def _rendered_text_image(lines: list[str]) -> bytes:
    image = Image.new("RGB", (600, 200), color="white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    y = 10
    for line in lines:
        draw.text((10, y), line, fill="black", font=font)
        y += 34
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestNativeTextPath:
    def test_a_pdf_with_a_real_text_layer_is_read_without_ocr(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text_liteparse(content, "invoice.pdf", "application/pdf")
        assert result.method == "pdf_text"
        assert "ABC Traders" in result.text
        assert "183254" in result.text

    def test_page_dimensions_are_reported(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text_liteparse(content, "invoice.pdf", "application/pdf")
        assert len(result.page_dimensions) == result.pages == 1
        width, height = result.page_dimensions[0]
        assert width > 0 and height > 0

    def test_positioned_words_are_returned_with_bboxes(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text_liteparse(content, "invoice.pdf", "application/pdf")
        words = result.words_by_page[0]
        texts = {w.text for w in words}
        assert "ABC" in texts or "Traders" in texts
        assert all(w.x1 > w.x0 and w.y1 > w.y0 for w in words)

    def test_a_sparse_but_real_text_layer_does_not_trigger_unnecessary_ocr(self) -> None:
        """Regression guard for a real bug found live during the migration:
        LiteParse's own complexity.needs_ocr flagged a real, clean, correct
        sample invoice as needing OCR (reason: 'sparse-text', since invoices
        are visually sparse relative to page area) and would have triggered
        an ~18-second unnecessary OCR pass. This project's own char-count
        heuristic is used instead, precisely to avoid that."""
        content = _native_text_pdf(["A single short line of real text."])
        result = extract_text_liteparse(content, "invoice.pdf", "application/pdf")
        assert result.method == "pdf_text"

    def test_an_unsupported_file_type_is_rejected_before_reaching_liteparse(self) -> None:
        """Regression guard for a real bug found live: a non-PDF/image
        upload used to get a clean UnsupportedFileType (-> 400 at the route
        layer); routing straight into LiteParse without this check first
        turned that into an opaque LiteParse ParseError (-> 502) instead,
        since LiteParse itself supports formats this project's upload
        contract never has and errors differently for ones it doesn't."""
        from ocr.models import UnsupportedFileType

        with pytest.raises(UnsupportedFileType):
            extract_text_liteparse(b"not a real file", "invoice.docx", "application/msword")


@requires_tesseract
class TestOcrPath:
    def test_an_image_with_no_native_text_is_routed_through_ocr(self) -> None:
        content = _rendered_text_image(["INVOICE NUMBER 1841", "TOTAL DUE 183254"])
        result = extract_text_liteparse(content, "receipt.png", "image/png")
        assert result.method == "ocr"
        assert result.pages == 1

    def test_ocr_path_words_have_bboxes_and_confidence(self) -> None:
        """Regression guard for a real bug found live: OCR-path TextItems
        are already word-level with an empty nested .words list (unlike the
        native-text path's span-level TextItems) — a naive flatten that
        only read .words silently produced zero PositionedWords despite
        .text having real content."""
        content = _rendered_text_image(["Some receipt text"])
        result = extract_text_liteparse(content, "receipt.png", "image/png")
        assert len(result.words_by_page[0]) > 0
        for word in result.words_by_page[0]:
            assert 0.0 <= word.confidence <= 1.0

    def test_page_dimensions_are_reported_for_the_ocr_path_too(self) -> None:
        content = _rendered_text_image(["Some receipt text"])
        result = extract_text_liteparse(content, "receipt.png", "image/png")
        assert len(result.page_dimensions) == 1
        assert result.page_dimensions[0][0] > 0


class TestGluedTextItemSplitting:
    """Regression guard for a real bug found live on a real e-commerce
    order-confirmation invoice: the OCR path's own per-word `.words`
    breakdown (see _flatten_words' docstring) was sometimes empty even
    though `item.text` itself held multiple space-separated words (e.g. a
    detected region read as "ORDER DATE" with no `.words` split) —
    _flatten_words previously treated that whole item as one
    PositionedWord, so invoice_extraction's label matching (which looks
    for a standalone "date" token) never found it glued inside "order
    date". Testing _flatten_words directly with a duck-typed fake
    TextItem, since forcing Tesseract itself to glue words this way isn't
    reproducible on demand."""

    def test_a_multi_word_text_item_with_no_words_breakdown_is_split_on_whitespace(self) -> None:
        import ocr.liteparse as liteparse_module

        class _FakeTextItem:
            words: list = []
            text = "ORDER DATE"
            x, y, width, height = 100.0, 200.0, 80.0, 10.0
            confidence = 0.9

        class _FakePage:
            page_num = 1
            text_items = [_FakeTextItem()]

        words_by_page = liteparse_module._flatten_words([_FakePage()])
        words = words_by_page[0]

        assert [w.text for w in words] == ["ORDER", "DATE"]
        assert all(w.confidence == 0.9 for w in words)
        # bbox width split proportionally by character count, laid out left to right
        assert words[0].x0 == 100.0
        assert words[0].x1 < words[1].x0 or words[0].x1 == words[1].x0
        assert words[1].x1 == 180.0

    def test_a_single_word_text_item_is_unaffected(self) -> None:
        import ocr.liteparse as liteparse_module

        class _FakeTextItem:
            words: list = []
            text = "ARGENTO"
            x, y, width, height = 10.0, 20.0, 40.0, 10.0
            confidence = 1.0

        class _FakePage:
            page_num = 1
            text_items = [_FakeTextItem()]

        words_by_page = liteparse_module._flatten_words([_FakePage()])
        assert [w.text for w in words_by_page[0]] == ["ARGENTO"]
        assert words_by_page[0][0].x0 == 10.0
        assert words_by_page[0][0].x1 == 50.0


class TestPaddleocrUrlConstruction:
    def test_a_base_url_gets_the_ocr_path_appended(self, monkeypatch) -> None:
        """Regression guard for a real bug found live in Docker: passing
        the paddleocr *service's base URL* (the convention every other
        internal service URL in this codebase uses, e.g.
        PADDLEOCR_SERVICE_URL=http://paddleocr:8008) straight through as
        LiteParse's ocr_server_url produced 'POST / 404 Not Found' against
        the real running service — LiteParse posts to exactly the URL it's
        given, it does not assume '/ocr' itself. The base URL must have
        '/ocr' appended in exactly one place."""
        import ocr.liteparse as liteparse_module

        captured = {}

        class _FakeLiteParse:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def parse(self, content):
                raise RuntimeError("stop before a real network call")

        monkeypatch.setattr(liteparse_module.liteparse, "LiteParse", _FakeLiteParse)

        try:
            liteparse_module._extract_with_ocr(b"fake", paddleocr_url="http://paddleocr:8008", ocr_language="en")
        except Exception:
            pass  # only the constructed kwargs matter for this test

        assert captured.get("ocr_server_url") == "http://paddleocr:8008/ocr"

    def test_a_trailing_slash_on_the_base_url_is_handled(self, monkeypatch) -> None:
        import ocr.liteparse as liteparse_module

        captured = {}

        class _FakeLiteParse:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def parse(self, content):
                raise RuntimeError("stop before a real network call")

        monkeypatch.setattr(liteparse_module.liteparse, "LiteParse", _FakeLiteParse)

        try:
            liteparse_module._extract_with_ocr(b"fake", paddleocr_url="http://paddleocr:8008/", ocr_language="en")
        except Exception:
            pass

        assert captured.get("ocr_server_url") == "http://paddleocr:8008/ocr"


class TestEngineSelection:
    def test_tesseract_engine_is_the_fully_unmodified_legacy_path(self) -> None:
        """The rollback switch: engine='tesseract' must behave identically
        to calling extract_text() directly — same function, not a
        liteparse-flavored reimplementation."""
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        direct = extract_text(content, "invoice.pdf")
        via_dispatch = extract_text_with_engine(content, "invoice.pdf", engine="tesseract")
        assert direct.method == via_dispatch.method
        assert direct.text == via_dispatch.text

    def test_liteparse_engine_is_the_default(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text_with_engine(content, "invoice.pdf")
        assert result.method == "pdf_text"
        assert "ABC Traders" in result.text

    def test_a_total_liteparse_failure_falls_back_to_the_legacy_pipeline(self, monkeypatch) -> None:
        """Even if LiteParse itself is broken (not just its OCR sub-paths —
        a genuine parse-level failure), the request must still succeed via
        the fully legacy pipeline rather than erroring. extract_text_with_engine
        imports ocr.liteparse.extract_text lazily inside the function body,
        so patching the source module's own attribute is what actually
        takes effect at call time."""
        import ocr.liteparse as liteparse_module

        def _raise(*args, **kwargs):
            raise LiteParseOcrError("simulated total LiteParse failure")

        monkeypatch.setattr(liteparse_module, "extract_text", _raise)

        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text_with_engine(content, "invoice.pdf", engine="liteparse")
        assert result.method == "pdf_text"  # the legacy PyMuPDF path still succeeds
        assert "ABC Traders" in result.text
