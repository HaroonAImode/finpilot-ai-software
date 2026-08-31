"""End-to-end extraction against real sample invoices, not just synthetic
ones — the same discipline this project has used everywhere else (see
docs/email-connector-plan.md's live-verification history). These two
fixtures found genuine bugs during development that no synthetic test would
have: a real multi-column header layout ("Bill From" / "Invoice No." /
"Currency" packed onto one visual row) that silently scrambled label
matching, and a right-aligned totals value with a large visual gap that a
naive gap-based cutoff would have rejected as "too far to be the value."
Both are fixed in fields.py; these tests are the regression guard for them.

tax_invoice_milestone.pdf is a clean, traditional tax invoice and extracts
well across the board — used as the "this is what success looks like" case.
shopify_billing_statement.pdf is a genuinely harder document (a subscription
billing statement, not a traditional vendor invoice — no explicit vendor
label, no line-item table in the normal sense) and is kept specifically
*because* it extracts imperfectly: it is the honest evidence for
docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md §0's stated
accuracy ceiling, not a bug to chase away by overfitting rules to one
document.
"""
import shutil
from pathlib import Path

import pytest

from ocr import extract_text
from invoice_extraction import extract_invoice

FIXTURES = Path(__file__).parent / "fixtures"

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not on PATH — see docs/invoice-ocr-plan.md §4.",
)


def _extract(filename: str):
    content = (FIXTURES / filename).read_bytes()
    ocr_result = extract_text(content, filename, mimetype="application/pdf")
    return extract_invoice(ocr_result)


_IMAGE_MIMETYPES = {".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".webp": "image/webp"}


def _extract_image(filename: str):
    content = (FIXTURES / filename).read_bytes()
    mimetype = _IMAGE_MIMETYPES[Path(filename).suffix.lower()]
    ocr_result = extract_text(content, filename, mimetype=mimetype)
    return extract_invoice(ocr_result)


class TestTaxInvoiceMilestone:
    """A clean, traditional single-line-item tax invoice — real vendor,
    real customer, real dollar amounts. This is the case rules-based
    extraction is supposed to nail, per the research's own framing that
    header fields on a normal invoice are the tractable part."""

    def test_vendor_name_is_correct_despite_a_packed_multi_column_header(self) -> None:
        """Regression guard: this document's 'BILL FROM:' / 'INVOICE NO.' /
        'CURRENCY' labels land on one visual row once grouped by y-position
        — before the fields.py fix, this returned 'INVOICE NO. CURRENCY'
        (a different column's label text) instead of the vendor name."""
        result = _extract("tax_invoice_milestone.pdf")
        assert result.vendor_name.value == "muhammad haroon"
        assert result.vendor_name.method == "label_anchor+pattern"

    def test_invoice_date_is_parsed(self) -> None:
        result = _extract("tax_invoice_milestone.pdf")
        assert result.invoice_date.value is not None
        assert result.invoice_date.value.isoformat() == "2025-05-06"

    def test_total_is_found_despite_a_large_same_line_gap(self) -> None:
        """Regression guard: 'Total' and '$100.00' sit ~124pt apart on the
        same row (right-aligned totals column) — a naive gap-based cutoff
        rejected this as 'too far to be the value' before the fix."""
        result = _extract("tax_invoice_milestone.pdf")
        assert result.total.value == 100.0
        assert result.total.method == "label_anchor+pattern"

    def test_the_single_line_item_is_reconstructed(self) -> None:
        result = _extract("tax_invoice_milestone.pdf")
        assert len(result.line_items) == 1
        assert result.line_items[0].description.value == "Initial Milestone"
        assert result.line_items[0].amount.value == 100.0

    def test_document_confidence_is_high_for_a_clean_native_pdf(self) -> None:
        result = _extract("tax_invoice_milestone.pdf")
        assert result.extraction_source == "pdf_text"
        assert result.document_confidence > 0.5

    def test_page_dimensions_pass_through_from_the_ocr_result(self) -> None:
        """page_dimensions is pure passthrough from Stage 1 (ocr.ExtractionResult)
        — the bounding-box review UI needs it to scale a field's bbox onto a
        rendered page image."""
        content = (FIXTURES / "tax_invoice_milestone.pdf").read_bytes()
        ocr_result = extract_text(content, "tax_invoice_milestone.pdf", mimetype="application/pdf")
        result = extract_invoice(ocr_result)
        assert result.page_dimensions == ocr_result.page_dimensions
        assert len(result.page_dimensions) >= 1


class TestShopifyBillingStatement:
    """A genuinely harder document — a subscription billing statement, not
    a traditional vendor invoice, with no explicit "vendor" or "invoice
    number" label anywhere. Originally kept as an example of where rules
    reach their limit (research report §0); a routing-logic audit against a
    real 35-receipt dataset found the vendor-extraction miss below was a
    real, fixable bug, not a rules ceiling — see this class's own tests."""

    def test_total_and_subtotal_are_still_found(self) -> None:
        """Money fields with a clear, unambiguous label still work even on
        an unusual document shape."""
        result = _extract("shopify_billing_statement.pdf")
        assert result.total.value == 1.0
        assert result.subtotal.value == 1.0

    def test_vendor_name_correctly_skips_past_the_date_issued_header_line(self) -> None:
        """Regression guard for a real bug this test class's own routing
        assertion below used to (unknowingly) rely on: with no explicit
        vendor label, the positional fallback used to land on this
        document's "Date issued Jul 28, 2026 Jul 28, 2026" header line
        instead of the real vendor two lines below — a roughly even
        letter/digit mix let it slip past the digit-ratio filter, and
        native PDF text is always 1.0 confidence so the confidence filter
        couldn't catch it either. find_vendor_name's positional fallback
        now also rejects a candidate line that is itself a recognizable
        date, and correctly reaches "Shopify Commerce Singapore Pte. Ltd."
        instead."""
        result = _extract("shopify_billing_statement.pdf")
        assert result.vendor_name.value == "Shopify Commerce Singapore Pte. Ltd."

    def test_no_invoice_number_no_longer_blocks_auto_processing_for_a_non_invoice_bill(self) -> None:
        """This document never labels anything 'invoice number' the way a
        traditional invoice does ('Bill #565151287' is glued into one token
        with no space, a genuine tokenization edge case) — but it is
        classified as a "bill", not a formal "invoice", and now that vendor
        extraction above is fixed, total/vendor/date are all correct and
        consistent (totals_pass, date_plausible). Requiring a numbered
        invoice reference on a subscription billing statement was an
        overly conservative bar, not a genuine risk signal — see
        docs/invoice-ocr-plan.md's routing-audit section for the real
        35-receipt evidence this was calibrated against."""
        result = _extract("shopify_billing_statement.pdf")
        assert result.review_status == "auto_processed"


@requires_tesseract
class TestRealReceipts:
    """Real photographed local-shop receipts, not synthetic ones — the
    actual documents that surfaced the receipt-robustness gaps
    (docs/invoice-ocr-plan.md's receipt-robustness notes). Both are
    genuinely hard cases (a phone photo, garbled OCR) — assertions here
    reflect what's actually, verifiably extracted today, not an aspirational
    target; each was individually confirmed correct against the real
    document before being written as an assertion.
    """

    def test_yz_paint_hardware_city_and_country_are_correctly_detected(self) -> None:
        result = _extract_image("yz_paint_hardware.jpeg")
        assert result.city.value == "Islamabad"
        assert result.country.value == "Pakistan"

    def test_yz_paint_hardware_does_not_hallucinate_a_currency(self) -> None:
        """Regression guard for the real false positive found live: garbled
        OCR ('$$ : ;', no digit adjacent) must not be read as USD evidence."""
        result = _extract_image("yz_paint_hardware.jpeg")
        assert result.currency.value is None

    def test_yz_paint_hardware_correctly_routes_to_review_not_false_confidence(self) -> None:
        """OCR quality on this real photo is too poor for most fields to be
        found at all — the honest, correct outcome is routing to review
        with low document confidence, not a confidently-wrong guess."""
        result = _extract_image("yz_paint_hardware.jpeg")
        assert result.review_status == "needs_review_high_priority"

    def test_samsuddin_invoice_document_type_city_and_country_are_correctly_detected(self) -> None:
        result = _extract_image("samsuddin_siddiqui.webp")
        assert result.document_type.value == "invoice"
        assert result.city.value == "Mumbai"
        assert result.country.value == "India"

    def test_samsuddin_invoices_currency_false_positive_is_reported_as_uncertain_not_fact(self) -> None:
        """Regression guard for the real false positive found live: a
        garbled company-logo mark OCR'd as '$9' on a document that is
        actually priced in Rs, not USD. Not fully filterable by pattern
        alone (it does look currency-shaped) — the honest fallback is
        reporting it at low enough confidence to route to review rather
        than presenting it as fact."""
        result = _extract_image("samsuddin_siddiqui.webp")
        if result.currency.value is not None:
            assert result.currency.confidence < 0.5


class TestExtractionIsDeterministic:
    @pytest.mark.parametrize("filename", ["tax_invoice_milestone.pdf", "shopify_billing_statement.pdf"])
    def test_running_extraction_twice_gives_identical_results(self, filename: str) -> None:
        """No randomness anywhere in this pipeline — same input must always
        produce the same output, unlike an LLM call would."""
        first = _extract(filename)
        second = _extract(filename)
        assert first.total.value == second.total.value
        assert first.vendor_name.value == second.vendor_name.value
        assert first.document_confidence == second.document_confidence
