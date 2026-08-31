"""extract_documents_with_engine — the multi-document-aware Stage 0+1 entry
point (docs/invoice-ocr-plan.md §7). `preprocess_image` and
`extract_text_with_engine` are mocked here so these tests isolate the
orchestration logic itself (PDF bypass, one-result vs many-result fan-out,
undecodable-image fallback) from the real CV/OCR machinery — those already
have their own dedicated test suites (test_preprocess.py, test_extract.py).
"""
from unittest.mock import patch

from ocr.extract import extract_documents_with_engine
from ocr.models import ExtractionResult
from ocr.preprocess import DetectedDocument


def _result(text: str) -> ExtractionResult:
    return ExtractionResult(text=text, confidence=0.9, method="ocr", pages=1, words_by_page=[[]], page_dimensions=[(100.0, 100.0)])


class TestPdfBypass:
    def test_a_pdf_skips_preprocessing_entirely_and_returns_one_result(self) -> None:
        with patch("ocr.extract.extract_text_with_engine", return_value=_result("pdf text")) as mock_extract, \
             patch("ocr.preprocess.preprocess_image") as mock_preprocess:
            results = extract_documents_with_engine(b"%PDF-fake", "invoice.pdf", "application/pdf")

        assert len(results) == 1
        assert results[0][0].text == "pdf text"
        assert results[0][1] == b"%PDF-fake"
        mock_preprocess.assert_not_called()
        mock_extract.assert_called_once()


class TestSingleDocumentImage:
    def test_one_detected_document_returns_one_result(self) -> None:
        cropped_bytes = b"cropped-png-bytes"
        with patch("ocr.preprocess.preprocess_image", return_value=[
            DetectedDocument(image_bytes=cropped_bytes, bbox=(10, 10, 90, 90), confidence=0.8),
        ]), patch("ocr.extract.extract_text_with_engine", return_value=_result("one document")) as mock_extract:
            results = extract_documents_with_engine(b"original-bytes", "receipt.jpg", "image/jpeg")

        assert len(results) == 1
        assert results[0] == (_result("one document"), cropped_bytes)
        mock_extract.assert_called_once()
        # The cropped bytes, not the original, are what actually gets OCR'd.
        assert mock_extract.call_args[0][0] == cropped_bytes


class TestMultiDocumentImage:
    def test_two_detected_documents_return_two_independent_results(self) -> None:
        doc_a, doc_b = b"doc-a-bytes", b"doc-b-bytes"
        with patch("ocr.preprocess.preprocess_image", return_value=[
            DetectedDocument(image_bytes=doc_a, bbox=(0, 0, 50, 100), confidence=0.7),
            DetectedDocument(image_bytes=doc_b, bbox=(60, 0, 110, 100), confidence=0.75),
        ]), patch("ocr.extract.extract_text_with_engine", side_effect=[_result("first"), _result("second")]) as mock_extract:
            results = extract_documents_with_engine(b"original-photo-bytes", "photo.jpg", "image/jpeg")

        assert len(results) == 2
        assert [r[0].text for r in results] == ["first", "second"]
        assert [r[1] for r in results] == [doc_a, doc_b]
        assert mock_extract.call_count == 2


class TestUndecodableImageFallback:
    def test_a_preprocessing_decode_failure_falls_back_to_the_original_bytes(self) -> None:
        with patch("ocr.preprocess.preprocess_image", side_effect=ValueError("corrupt image")), \
             patch("ocr.extract.extract_text_with_engine", return_value=_result("fallback")) as mock_extract:
            results = extract_documents_with_engine(b"corrupt-but-classified-as-image", "weird.png", "image/png")

        assert len(results) == 1
        assert results[0] == (_result("fallback"), b"corrupt-but-classified-as-image")
        mock_extract.assert_called_once_with(
            b"corrupt-but-classified-as-image", "weird.png", "image/png",
            engine="liteparse", paddleocr_url=None, ocr_language="en",
        )
