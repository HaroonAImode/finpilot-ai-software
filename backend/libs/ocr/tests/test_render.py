"""ocr.render.render_page_png — the bounding-box review UI's source image.
Reuses the same real-PDF/real-image fixture generators as test_extract.py
rather than checked-in files.
"""
import io

import fitz
import pytest
from PIL import Image

from ocr.models import UnsupportedFileType
from ocr.render import PageOutOfRange, render_page_png

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _native_text_pdf(pages: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), "Vendor: ABC Traders", fontsize=12)
    content = doc.tobytes()
    doc.close()
    return content


def _plain_image(fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (300, 150), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


class TestRenderPdfPage:
    def test_a_valid_page_renders_to_png_bytes(self) -> None:
        content = _native_text_pdf(pages=1)
        png_bytes = render_page_png(content, "invoice.pdf", "application/pdf", page=0)
        assert png_bytes[:8] == _PNG_MAGIC

    def test_a_later_page_of_a_multi_page_pdf_also_renders(self) -> None:
        content = _native_text_pdf(pages=3)
        png_bytes = render_page_png(content, "invoice.pdf", "application/pdf", page=2)
        assert png_bytes[:8] == _PNG_MAGIC

    def test_a_negative_page_is_rejected(self) -> None:
        content = _native_text_pdf(pages=1)
        with pytest.raises(PageOutOfRange):
            render_page_png(content, "invoice.pdf", "application/pdf", page=-1)

    def test_a_page_past_the_end_is_rejected(self) -> None:
        content = _native_text_pdf(pages=1)
        with pytest.raises(PageOutOfRange):
            render_page_png(content, "invoice.pdf", "application/pdf", page=1)


class TestRenderImagePage:
    def test_page_zero_of_an_image_upload_renders_to_png(self) -> None:
        content = _plain_image("JPEG")
        png_bytes = render_page_png(content, "receipt.jpg", "image/jpeg", page=0)
        assert png_bytes[:8] == _PNG_MAGIC

    def test_any_page_other_than_zero_is_rejected_for_an_image(self) -> None:
        content = _plain_image("PNG")
        with pytest.raises(PageOutOfRange):
            render_page_png(content, "receipt.png", "image/png", page=1)


class TestRenderUnsupportedFile:
    def test_an_unrecognized_file_type_is_rejected_clearly(self) -> None:
        with pytest.raises(UnsupportedFileType):
            render_page_png(b"not a real file", "invoice.docx", None, page=0)
