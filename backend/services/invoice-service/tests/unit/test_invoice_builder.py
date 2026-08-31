"""Maps an AI Engine extraction result onto Invoice/InvoiceItem — pure
function, no DB needed, so every review_status/field-mapping edge case is
covered directly here rather than only indirectly through the route tests.
"""
import uuid

from app.models import InvoiceStatus, InvoiceType
from app.schemas.ai_extraction import (
    ArithmeticValidationSchema, ExtractedFieldSchema, ExtractedInvoiceSchema, ExtractedLineItemSchema,
)
from app.services.invoice_builder import build_invoice

COMPANY_ID = uuid.uuid4()


def _field(value=None, confidence=1.0, method="label_anchor+pattern") -> ExtractedFieldSchema:
    return ExtractedFieldSchema(value=value, confidence=confidence, method=method)


def _extraction(**overrides) -> ExtractedInvoiceSchema:
    defaults = dict(
        vendor_name=_field("ABC Traders"),
        invoice_number=_field("INV-1841"),
        invoice_date=_field("2026-08-04"),
        ntn=_field("3947261-8"),
        subtotal=_field(155300.0),
        tax_rate=_field(0.18),
        tax_amount=_field(27954.0),
        total=_field(183254.0),
        line_items=[],
        arithmetic_validation=ArithmeticValidationSchema(
            line_items_pass_rate=None, totals_pass=True, tax_rate_plausible=True, date_plausible=True,
        ),
        document_confidence=0.95,
        extraction_source="pdf_text",
        review_status="auto_processed",
        review_flags=[],
    )
    defaults.update(overrides)
    return ExtractedInvoiceSchema(**defaults)


def _build(*, invoice_type: InvoiceType = InvoiceType.purchase, **overrides):
    return build_invoice(
        company_id=COMPANY_ID, extraction=_extraction(**overrides), filename="invoice.pdf",
        mimetype="application/pdf", size=1000, s3_key="abc/invoice.pdf", file_hash="deadbeef",
        invoice_type=invoice_type,
    )


class TestFieldMapping:
    def test_all_scalar_fields_map_through(self) -> None:
        invoice = _build()
        assert invoice.vendor_name == "ABC Traders"
        assert invoice.invoice_number == "INV-1841"
        assert invoice.invoice_date.isoformat() == "2026-08-04"
        assert invoice.ntn == "3947261-8"
        assert invoice.subtotal == 155300.0
        assert invoice.tax_rate == 0.18
        assert invoice.tax_amount == 27954.0
        assert invoice.total == 183254.0

    def test_missing_fields_become_none_not_a_default_value(self) -> None:
        """A field the rules engine genuinely couldn't find must stay None
        — coercing it to 0/'' would silently invent data that isn't there."""
        invoice = _build(vendor_name=_field(None), total=_field(None))
        assert invoice.vendor_name is None
        assert invoice.total is None

    def test_defaults_to_purchase_when_invoice_type_is_not_given(self) -> None:
        """The original and still most common caller, scanner.py, never
        passes invoice_type at all — every existing call site must keep
        producing a purchase invoice with no code change on its part.
        (Sale is no longer purchase-only reserved: sales_scanner.py's
        scan-to-revenue path passes invoice_type=sale explicitly — see
        TestSaleInvoiceType below.)"""
        assert _build().type == InvoiceType.purchase

    def test_metadata_fields_are_carried_through_unedited(self) -> None:
        invoice = _build()
        assert invoice.document_confidence == 0.95
        assert invoice.extraction_source == "pdf_text"
        assert invoice.filename == "invoice.pdf"
        assert invoice.file_hash == "deadbeef"

    def test_the_full_extraction_result_is_preserved_as_raw_json(self) -> None:
        """Rule 8.1: the raw extraction must be fully recoverable, since a
        later human correction must never overwrite it."""
        invoice = _build()
        assert invoice.raw_extraction_json["vendor_name"]["value"] == "ABC Traders"
        assert invoice.raw_extraction_json["review_status"] == "auto_processed"


class TestFieldConfidence:
    """Invoice.field_confidence — the property the review UI reads to show
    per-field trust, not just one document-level number (Rule 8.2)."""

    def test_found_fields_report_their_own_confidence(self) -> None:
        invoice = _build(
            vendor_name=_field("ABC Traders", confidence=0.95),
            total=_field(183254.0, confidence=0.6),
        )
        assert invoice.field_confidence["vendor_name"] == 0.95
        assert invoice.field_confidence["total"] == 0.6

    def test_a_field_the_engine_never_found_has_no_entry_at_all(self) -> None:
        """Missing from the dict, not present with confidence 0 — "not
        found" and "found but uncertain" are different signals the UI must
        be able to tell apart."""
        invoice = _build(ntn=_field(None, confidence=0.0, method="not_found"))
        assert "ntn" not in invoice.field_confidence


class TestFieldLocations:
    """Invoice.field_locations — the property the bounding-box review UI
    reads to know where on the document each field came from (the second
    scanner experience)."""

    def test_found_fields_report_their_page_and_bbox(self) -> None:
        invoice = _build(
            vendor_name=ExtractedFieldSchema(
                value="ABC Traders", confidence=0.95, method="label_anchor+pattern",
                page=0, bbox=(10.0, 20.0, 100.0, 40.0),
            ),
        )
        assert invoice.field_locations["vendor_name"] == {"page": 0, "bbox": [10.0, 20.0, 100.0, 40.0]}

    def test_a_field_the_engine_never_found_has_no_entry_at_all(self) -> None:
        invoice = _build(ntn=_field(None))
        assert "ntn" not in invoice.field_locations

    def test_a_found_field_with_no_location_data_has_no_entry(self) -> None:
        """A field can be found without a page/bbox (e.g. the computed
        tax_rate field, which has no label anchor of its own) — that's not
        an error, just nothing to draw a box for."""
        invoice = _build()  # every default _field() has page=None, bbox=None
        assert invoice.field_locations == {}


class TestPageDimensions:
    def test_page_dimensions_pass_through_from_raw_extraction(self) -> None:
        invoice = _build(page_dimensions=[(612.0, 792.0)])
        assert invoice.page_dimensions == [[612.0, 792.0]]

    def test_missing_page_dimensions_is_an_empty_list_not_an_error(self) -> None:
        """An invoice scanned before this field existed has no
        page_dimensions in its stored raw_extraction_json at all — the
        review UI must treat that as "grounding not available", not crash."""
        invoice = _build()
        assert invoice.page_dimensions == []


class TestExtractedFields:
    """Invoice.extracted_fields — the receipt/bill robustness additions
    (customer_name, currency, document_type, payment_status, city,
    country). Read-only for now; same exclude-when-absent discipline as
    field_confidence."""

    def test_a_found_field_is_reported_with_value_confidence_and_status(self) -> None:
        invoice = _build(currency=_field("PKR", confidence=1.0))
        assert invoice.extracted_fields["currency"] == {"value": "PKR", "confidence": 1.0, "status": "FOUND"}

    def test_a_field_the_engine_never_found_is_absent_not_a_fabricated_default(self) -> None:
        """The exact discipline this whole pass exists to enforce: no
        currency/country/payment-status field is ever invented when there's
        no evidence — it's simply missing from this dict."""
        invoice = _build()  # every new field defaults to not_found in the schema
        assert "currency" not in invoice.extracted_fields
        assert "country" not in invoice.extracted_fields
        assert "payment_status" not in invoice.extracted_fields
        assert "document_type" not in invoice.extracted_fields
        assert "customer_name" not in invoice.extracted_fields
        assert "city" not in invoice.extracted_fields

    def test_multiple_found_fields_all_appear(self) -> None:
        invoice = _build(
            currency=_field("PKR"), city=_field("Islamabad"), country=_field("Pakistan"),
        )
        assert set(invoice.extracted_fields) == {"currency", "city", "country"}


class TestReviewStatusMapping:
    def test_auto_processed_maps_to_processed(self) -> None:
        assert _build(review_status="auto_processed").status == InvoiceStatus.processed

    def test_needs_review_maps_directly(self) -> None:
        assert _build(review_status="needs_review").status == InvoiceStatus.needs_review

    def test_needs_review_high_priority_maps_directly(self) -> None:
        assert _build(review_status="needs_review_high_priority").status == InvoiceStatus.needs_review_high_priority


class TestLineItems:
    def test_line_items_map_in_original_order(self) -> None:
        items = [
            ExtractedLineItemSchema(
                description=_field("Steel Sheet"), qty=_field(12.0), rate=_field(9800.0), amount=_field(117600.0),
                arithmetic_check="pass", review_flags=[],
            ),
            ExtractedLineItemSchema(
                description=_field("Transport"), qty=_field(1.0), rate=_field(8500.0), amount=_field(8500.0),
                arithmetic_check="pass", review_flags=[],
            ),
        ]
        invoice = _build(line_items=items)

        assert [i.position for i in invoice.items] == [0, 1]
        assert invoice.items[0].description == "Steel Sheet"
        assert invoice.items[1].amount == 8500.0

    def test_a_failed_arithmetic_check_and_its_flags_carry_through(self) -> None:
        items = [
            ExtractedLineItemSchema(
                description=_field("Mismatched row"), qty=_field(2.0), rate=_field(100.0), amount=_field(999.0),
                arithmetic_check="fail", review_flags=["qty_rate_amount_mismatch"],
            ),
        ]
        invoice = _build(line_items=items)
        assert invoice.items[0].arithmetic_check == "fail"
        assert invoice.items[0].review_flags == ["qty_rate_amount_mismatch"]

    def test_no_line_items_is_a_valid_empty_list_not_an_error(self) -> None:
        assert _build(line_items=[]).items == []


class TestSaleInvoiceType:
    """Revenue Manager's scan-to-revenue path
    (docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md §6.2)
    — the same extraction result, aimed at type=sale instead of purchase."""

    def test_invoice_type_is_set_on_the_row(self) -> None:
        invoice = _build(invoice_type=InvoiceType.sale)
        assert invoice.type == InvoiceType.sale

    def test_customer_name_is_taken_from_the_dedicated_field_when_present(self) -> None:
        invoice = _build(
            invoice_type=InvoiceType.sale,
            customer_name=_field("Al-Madina Retail"), vendor_name=_field("Some Other Name"),
        )
        assert invoice.customer_name == "Al-Madina Retail"
        # vendor_name is still set unconditionally — its confidence/bbox
        # keep working for the bounding-box overlay with no special-casing.
        assert invoice.vendor_name == "Some Other Name"

    def test_customer_name_falls_back_to_vendor_name_when_the_dedicated_field_is_empty(self) -> None:
        """The rules engine's counterparty-name heuristic reports under
        `vendor_name` regardless of which party it actually found — on a
        sale that is usually the customer. customer_name is a secondary,
        less reliable detector, so its absence should not leave the row
        with no counterparty at all when vendor_name did find one."""
        invoice = _build(invoice_type=InvoiceType.sale, customer_name=_field(None), vendor_name=_field("Al-Madina Retail"))
        assert invoice.customer_name == "Al-Madina Retail"

    def test_customer_name_is_none_when_neither_field_found_anything(self) -> None:
        invoice = _build(invoice_type=InvoiceType.sale, customer_name=_field(None), vendor_name=_field(None))
        assert invoice.customer_name is None

    def test_a_purchase_invoice_never_gets_a_customer_name(self) -> None:
        """customer_name on a purchase row would misrepresent who the
        counterparty actually is — a purchase's counterparty is a vendor,
        full stop, even if vendor_name happens to be populated."""
        invoice = _build(invoice_type=InvoiceType.purchase, vendor_name=_field("ABC Traders"))
        assert invoice.customer_name is None


class TestCategoryClassification:
    """Saved Records' cashbook category is best-guessed at scan time from
    the vendor name / line items / filename (category_classifier.py) —
    purchases only, never a verdict, always still human-editable
    afterward."""

    def test_a_recognized_vendor_gets_auto_categorized(self) -> None:
        invoice = _build(vendor_name=_field("KFC Gulberg"))
        assert invoice.category == "Office Entertainment"

    def test_an_unrecognized_vendor_is_left_uncategorized_not_guessed(self) -> None:
        invoice = _build(vendor_name=_field("Unrelated Traders Pvt Ltd"))
        assert invoice.category is None

    def test_a_sale_invoice_is_never_auto_categorized(self) -> None:
        """A sale has no cashbook category — it has a customer and line
        items, not a purchase expense bucket — even if vendor_name happens
        to contain a recognizable word."""
        invoice = _build(invoice_type=InvoiceType.sale, vendor_name=_field("KFC Gulberg"))
        assert invoice.category is None

    def test_line_item_descriptions_can_drive_the_category_too(self) -> None:
        items = [
            ExtractedLineItemSchema(
                description=_field("Coursera course fee"), qty=_field(1.0), rate=_field(5960.0), amount=_field(5960.0),
                arithmetic_check="pass", review_flags=[],
            ),
        ]
        invoice = _build(vendor_name=_field("Generic Traders"), line_items=items)
        assert invoice.category == "Employee Training & Education"


class TestNonTransactionalDocuments:
    """"Is this a financial transaction document?" is answered before "which
    cashbook category does it belong to?" — see build_invoice."""

    def test_a_non_transactional_document_is_never_given_a_cashbook_category(self) -> None:
        """The specific outcome this feature exists to prevent: an internal
        memo landing in the cashbook as "Uncategorized", which loses what
        the document actually is and reads as a categorization failure."""
        invoice = _build(
            transactional=False,
            document_type=_field("minute_sheet"),
            amount_mentioned=22875.0,
            total=_field(None, confidence=0.0, method="not_found"),
            # A vendor name that WOULD otherwise match a category keyword,
            # proving the guard is the transactional flag and not an
            # accident of this document's wording.
            vendor_name=_field("KFC Bahria"),
            review_status="needs_review",
        )
        assert invoice.transaction_status.value == "non_transactional"
        assert invoice.category is None

    def test_the_stated_amount_is_kept_but_never_as_the_total(self) -> None:
        invoice = _build(
            transactional=False,
            document_type=_field("minute_sheet"),
            amount_mentioned=22875.0,
            total=_field(None, confidence=0.0, method="not_found"),
            review_status="needs_review",
        )
        assert invoice.amount_mentioned == 22875.0
        assert invoice.total is None

    def test_classification_source_starts_as_the_rules_own_verdict(self) -> None:
        invoice = _build(transactional=False, document_type=_field("minute_sheet"), review_status="needs_review")
        assert invoice.classification_source.value == "rule"

    def test_a_normal_purchase_invoice_is_unaffected(self) -> None:
        """Regression guard: everything about the existing transactional
        path must behave exactly as it did before this feature."""
        invoice = _build(vendor_name=_field("KFC Bahria"))
        assert invoice.transaction_status.value == "transactional"
        assert invoice.classification_source.value == "rule"
        assert invoice.total == 183254.0
        assert invoice.amount_mentioned is None
        assert invoice.category == "Office Entertainment"

    def test_an_extraction_without_the_new_fields_defaults_to_transactional(self) -> None:
        """A document scanned before this existed, replayed through the
        builder, must not be reclassified retroactively."""
        invoice = _build()
        assert invoice.transaction_status.value == "transactional"
        assert invoice.amount_mentioned is None
