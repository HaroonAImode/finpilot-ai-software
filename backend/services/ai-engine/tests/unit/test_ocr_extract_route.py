"""POST /api/v1/ai/ocr/extract — Phase 2b's whole surface: wires Stage 1
(ocr) and Stage 2 (invoice_extraction) behind one HTTP call. Uses the same
real sample invoice as invoice_extraction's own tests (not a fresh
synthetic one) so this is a genuine end-to-end check of the wiring, not a
duplicate of logic already unit-tested in the libraries themselves.
"""
import base64
import io
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from ocr.models import ExtractionResult

FIXTURES = Path(__file__).parent.parent / "fixtures"
client = TestClient(app)


def _blank_png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _empty_result() -> ExtractionResult:
    # What Document Preprocessing/OCR actually find is irrelevant to these
    # tests — they check that the route correctly wires whatever
    # extract_documents_with_engine returns into the response shape, a
    # concern this project's convention keeps separate from the real
    # CV/OCR correctness already covered by libs/ocr's own test suites
    # (test_preprocess.py, test_extract_documents.py). Real OCR needs a
    # system tesseract binary this dev host does not have on PATH — the
    # same reason test_extract.py's own OCR-path tests skip without it.
    return ExtractionResult(text="", confidence=0.0, method="ocr", pages=1, words_by_page=[[]], page_dimensions=[(10.0, 10.0)])


class TestHealth:
    def test_health_check(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestExtractEndpoint:
    def test_a_real_invoice_is_extracted_end_to_end(self) -> None:
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()

        response = client.post(
            "/api/v1/ai/ocr/extract",
            files={"file": ("invoice.pdf", content, "application/pdf")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["vendor_name"]["value"] == "muhammad haroon"
        assert body["total"]["value"] == 100.0
        assert body["extraction_source"] == "pdf_text"
        assert len(body["line_items"]) == 1
        assert len(body["page_dimensions"]) >= 1

    def test_an_unsupported_file_type_is_a_clean_400(self) -> None:
        response = client.post(
            "/api/v1/ai/ocr/extract",
            files={"file": ("invoice.docx", b"not a real file", "application/msword")},
        )
        assert response.status_code == 400

    def test_an_oversized_file_is_rejected_before_extraction_runs(self, monkeypatch) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
        try:
            content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()
            response = client.post(
                "/api/v1/ai/ocr/extract",
                files={"file": ("invoice.pdf", content, "application/pdf")},
            )
            assert response.status_code == 413
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_known_vendors_enables_fuzzy_matching(self) -> None:
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()

        response = client.post(
            "/api/v1/ai/ocr/extract",
            files={"file": ("invoice.pdf", content, "application/pdf")},
            data={"known_vendors": json.dumps(["Muhammad Haroon Pvt Ltd"])},
        )

        assert response.status_code == 200
        vendor = response.json()["vendor_name"]
        # Rule 7.3's corporate-suffix stripping normalizes "Muhammad Haroon
        # Pvt Ltd" to the same string the extracted "muhammad haroon"
        # matches against — the resolved value is the known vendor's
        # original (non-normalized) name, not the raw extraction.
        assert vendor["value"] == "Muhammad Haroon Pvt Ltd"
        assert "vendor_match" in vendor["method"]

    def test_malformed_known_vendors_is_a_clean_400(self) -> None:
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()

        response = client.post(
            "/api/v1/ai/ocr/extract",
            files={"file": ("invoice.pdf", content, "application/pdf")},
            data={"known_vendors": "not valid json"},
        )

        assert response.status_code == 400

    def test_a_pdf_never_has_additional_documents(self) -> None:
        """PDFs are not preprocessed at all (docs/invoice-ocr-plan.md §7) —
        this is the strongest confirmation that Document Preprocessing is
        purely additive, not a regression on the existing PDF path."""
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()
        response = client.post(
            "/api/v1/ai/ocr/extract", files={"file": ("invoice.pdf", content, "application/pdf")},
        )
        assert response.status_code == 200
        assert response.json()["additional_documents"] == []


class TestDocumentPreprocessingIntegration:
    """Document Preprocessing (docs/invoice-ocr-plan.md §7) wired into the
    real endpoint. `extract_documents_with_engine` is mocked here — the CV
    logic itself is already thoroughly unit-tested against real OpenCV in
    libs/ocr/tests/test_preprocess.py, and the fan-out orchestration in
    libs/ocr/tests/test_extract_documents.py; this is only about the
    endpoint correctly surfacing whatever that function returns.
    """

    def test_a_single_document_result_has_no_additional_documents(self) -> None:
        with patch(
            "app.api.routes.ocr.extract_documents_with_engine",
            return_value=[(_empty_result(), _blank_png_bytes())],
        ):
            response = client.post(
                "/api/v1/ai/ocr/extract", files={"file": ("receipt.png", _blank_png_bytes(), "image/png")},
            )

        assert response.status_code == 200
        assert response.json()["additional_documents"] == []

    def test_two_detected_documents_produce_one_primary_and_one_additional_result(self) -> None:
        second_doc_bytes = _blank_png_bytes()
        with patch(
            "app.api.routes.ocr.extract_documents_with_engine",
            return_value=[(_empty_result(), _blank_png_bytes()), (_empty_result(), second_doc_bytes)],
        ):
            response = client.post(
                "/api/v1/ai/ocr/extract", files={"file": ("photo.jpg", _blank_png_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        body = response.json()
        # The primary result is still a complete, ordinary extraction
        # response — same shape as any single-document upload.
        assert "vendor_name" in body
        assert len(body["additional_documents"]) == 1

        additional = body["additional_documents"][0]
        assert additional["filename"] == "photo_doc_2.png"
        assert additional["mimetype"] == "image/png"
        assert base64.b64decode(additional["image_base64"]) == second_doc_bytes
        # The split-out document got its own full extraction response shape.
        assert "vendor_name" in additional["extracted"]

    def test_three_detected_documents_are_named_and_ordered_correctly(self) -> None:
        with patch(
            "app.api.routes.ocr.extract_documents_with_engine",
            return_value=[(_empty_result(), _blank_png_bytes()) for _ in range(3)],
        ):
            response = client.post(
                "/api/v1/ai/ocr/extract", files={"file": ("photo.jpg", _blank_png_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        filenames = [d["filename"] for d in response.json()["additional_documents"]]
        assert filenames == ["photo_doc_2.png", "photo_doc_3.png"]


class TestAutoCropEndpoint:
    def test_a_valid_image_with_nothing_confident_to_crop_is_returned_untouched(self) -> None:
        # A tiny blank white image has no real document-shaped content —
        # preprocess_image's own "never guess" contract means the original
        # bytes come back byte-for-byte, exactly as they would for any
        # other caller of that same function.
        original = _blank_png_bytes()
        response = client.post(
            "/api/v1/ai/ocr/auto-crop", files={"file": ("photo.jpg", original, "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == original

    def test_a_confidently_detected_crop_is_returned_as_the_response_body(self) -> None:
        # Route-wiring only — preprocess_image's own real CV correctness is
        # already covered by libs/ocr's own test_preprocess.py, matching
        # this file's existing convention (see TestDocumentPreprocessingIntegration's
        # own docstring reasoning above) of mocking the extraction/
        # preprocessing call itself rather than re-testing its internals here.
        from ocr.preprocess import DetectedDocument

        cropped_bytes = b"cropped-png-bytes"
        with patch(
            "app.api.routes.ocr.preprocess_image",
            return_value=[DetectedDocument(image_bytes=cropped_bytes, bbox=(5, 5, 50, 50), confidence=0.9)],
        ):
            response = client.post(
                "/api/v1/ai/ocr/auto-crop", files={"file": ("photo.jpg", _blank_png_bytes(), "image/jpeg")},
            )
        assert response.status_code == 200
        assert response.content == cropped_bytes

    def test_undecodable_content_is_a_clean_400(self) -> None:
        response = client.post(
            "/api/v1/ai/ocr/auto-crop", files={"file": ("photo.jpg", b"not a real image", "image/jpeg")},
        )
        assert response.status_code == 400

    def test_an_oversized_file_is_rejected_before_cropping_runs(self, monkeypatch) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
        try:
            response = client.post(
                "/api/v1/ai/ocr/auto-crop",
                files={"file": ("photo.jpg", _blank_png_bytes(), "image/jpeg")},
            )
            assert response.status_code == 413
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
