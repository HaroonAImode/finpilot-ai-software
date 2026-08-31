"""POST /api/v1/ai/ocr/render-page — the bounding-box review UI's document
image. Uses the same real sample invoice as the /extract tests."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent.parent / "fixtures"
client = TestClient(app)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestRenderPageEndpoint:
    def test_renders_a_pdf_page_as_png(self) -> None:
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()
        response = client.post(
            "/api/v1/ai/ocr/render-page",
            files={"file": ("invoice.pdf", content, "application/pdf")},
            data={"page": "0"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == _PNG_MAGIC

    def test_an_out_of_range_page_is_a_clean_400(self) -> None:
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()
        response = client.post(
            "/api/v1/ai/ocr/render-page",
            files={"file": ("invoice.pdf", content, "application/pdf")},
            data={"page": "99"},
        )
        assert response.status_code == 400

    def test_an_unsupported_file_type_is_a_clean_400(self) -> None:
        response = client.post(
            "/api/v1/ai/ocr/render-page",
            files={"file": ("invoice.docx", b"not a real file", "application/msword")},
            data={"page": "0"},
        )
        assert response.status_code == 400
