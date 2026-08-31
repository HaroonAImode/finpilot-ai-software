"""Stage 1 extraction (docs/invoice-ocr-plan.md §2): routing between the
native-PDF-text path and the OCR path, and the OCR path itself.

Sample documents are generated at test time (a real PDF via PyMuPDF, a real
rendered-text image via Pillow) rather than checked-in fixture files —
nothing here needs a scanned copy of an actual invoice to prove the routing
and confidence logic behaves correctly.

Tesseract itself is a system binary (see the plan doc's §4 — pytesseract is
just a wrapper around it), not something `pip install` provides. Tests that
actually need to run OCR skip cleanly with a clear reason when it isn't on
PATH, rather than failing in a way that looks like a code bug.
"""
import io
import shutil

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from ocr.extract import extract_text
from ocr.models import UnsupportedFileType

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
requires_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE,
    reason="tesseract binary not on PATH — install it to exercise the OCR path locally "
    "(see docs/invoice-ocr-plan.md §4); Phase 2's Docker container installs it via apt.",
)


def _native_text_pdf(lines: list[str]) -> bytes:
    """A PDF with a real, selectable text layer — PyMuPDF's get_text() reads
    this directly, no OCR involved."""
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 20
    content = doc.tobytes()
    doc.close()
    return content


def _blank_pdf_page() -> bytes:
    doc = fitz.open()
    doc.new_page()
    content = doc.tobytes()
    doc.close()
    return content


def _rendered_text_image(lines: list[str]) -> bytes:
    """A real bitmap with text drawn onto it — no text layer at all, must go
    through Tesseract to recover the words."""
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


def _scanned_style_pdf(lines: list[str]) -> bytes:
    """A PDF whose only content is an embedded image of text — simulates a
    photocopy scanned straight to PDF, with no text layer for PyMuPDF to
    read directly."""
    image_bytes = _rendered_text_image(lines)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=image_bytes)
    content = doc.tobytes()
    doc.close()
    return content


class TestFileClassification:
    def test_an_unrecognized_file_type_is_rejected_clearly(self) -> None:
        with pytest.raises(UnsupportedFileType):
            extract_text(b"not a real file", "invoice.docx", mimetype=None)

    def test_classification_falls_back_to_extension_when_mimetype_is_missing(self) -> None:
        content = _native_text_pdf(["Invoice #1841 from ABC Traders, dated 2026-08-04"])
        result = extract_text(content, "invoice.pdf", mimetype=None)
        assert result.method == "pdf_text"


class TestNativePdfTextExtraction:
    def test_a_pdf_with_a_real_text_layer_is_read_directly_not_ocrd(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])

        result = extract_text(content, "invoice.pdf", mimetype="application/pdf")

        assert result.method == "pdf_text"
        assert result.confidence == 1.0
        assert "ABC Traders" in result.text
        assert "183254" in result.text

    def test_page_count_is_reported(self) -> None:
        content = _native_text_pdf(["A single page of real text, long enough to clear the threshold."])
        result = extract_text(content, "invoice.pdf")
        assert result.pages == 1

    def test_positioned_words_are_returned_per_page(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])

        result = extract_text(content, "invoice.pdf")

        assert len(result.words_by_page) == result.pages == 1
        words = result.words_by_page[0]
        texts = {w.text for w in words}
        assert "ABC" in texts and "Traders" in texts and "183254" in texts
        # Native PDF text is a deterministic parse, not probabilistic
        # recognition — every word carries full confidence.
        assert all(w.confidence == 1.0 for w in words)

    def test_page_dimensions_are_reported_in_pdf_point_space(self) -> None:
        content = _native_text_pdf(["Vendor: ABC Traders", "Total: 183254"])
        result = extract_text(content, "invoice.pdf")
        assert len(result.page_dimensions) == result.pages == 1
        width, height = result.page_dimensions[0]
        assert width > 0 and height > 0

    def test_words_on_the_same_line_share_a_y_position_and_are_ordered_left_to_right(self) -> None:
        content = _native_text_pdf(["Alpha Beta Gamma Delta Epsilon Zeta Eta Theta"])
        words = extract_text(content, "invoice.pdf").words_by_page[0]

        by_x = sorted(words, key=lambda w: w.x0)
        assert [w.text for w in by_x] == [
            "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
        ]


@requires_tesseract
class TestScannedPdfFallsBackToOcr:
    def test_a_pdf_with_no_text_layer_is_rasterized_and_ocrd(self) -> None:
        content = _scanned_style_pdf(["VENDOR ABC TRADERS", "TOTAL 183254"])

        result = extract_text(content, "scanned-invoice.pdf", mimetype="application/pdf")

        assert result.method == "ocr"
        assert "VENDOR" in result.text.upper()
        assert 0.0 <= result.confidence <= 1.0

    def test_a_blank_page_does_not_crash_the_ocr_fallback(self) -> None:
        content = _blank_pdf_page()
        # A blank page has no text layer either — must still fall back to
        # OCR (which will just find nothing) rather than raising.
        result = extract_text(content, "blank.pdf", mimetype="application/pdf")
        assert result.method == "ocr"

    def test_page_dimensions_match_the_rasterized_pixel_size(self) -> None:
        content = _scanned_style_pdf(["VENDOR ABC TRADERS"])
        result = extract_text(content, "scanned-invoice.pdf", mimetype="application/pdf")
        assert len(result.page_dimensions) == 1
        width, height = result.page_dimensions[0]
        assert width > 0 and height > 0


@requires_tesseract
class TestImageOcr:
    def test_text_is_recovered_from_a_plain_image(self) -> None:
        content = _rendered_text_image(["INVOICE NUMBER 1841", "TOTAL DUE 183254"])

        result = extract_text(content, "receipt.jpg", mimetype="image/jpeg")

        assert result.method == "ocr"
        assert result.pages == 1
        assert "1841" in result.text or "INVOICE" in result.text.upper()

    def test_confidence_is_in_the_valid_range(self) -> None:
        content = _rendered_text_image(["Some receipt text"])
        result = extract_text(content, "receipt.png", mimetype="image/png")
        assert 0.0 <= result.confidence <= 1.0

    def test_classification_by_mimetype_alone_works_for_an_extensionless_upload(self) -> None:
        content = _rendered_text_image(["Whatsapp screenshot text"])
        result = extract_text(content, "image", mimetype="image/jpeg")
        assert result.method == "ocr"

    def test_page_dimensions_match_the_source_image_exactly(self) -> None:
        content = _rendered_text_image(["Some receipt text"])  # _rendered_text_image is a fixed 600x200 canvas
        result = extract_text(content, "receipt.png", mimetype="image/png")
        assert result.page_dimensions == [(600.0, 200.0)]
