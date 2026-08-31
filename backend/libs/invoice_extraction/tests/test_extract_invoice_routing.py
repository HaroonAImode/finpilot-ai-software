"""Routing-decision coverage — confidence.route()'s two inputs
(critical_fields_present, arithmetic_ok) as computed in extract_invoice.py,
direct unit coverage of the mechanism itself rather than only through real
document fixtures. Both real bugs these tests guard were found live during
a routing-logic audit against a real 35-receipt dataset: every one of those
35 informal receipts was routed to needs_review_high_priority regardless of
how accurate its extraction actually was, because invoice_number (never
found on an informal receipt) was being treated as critical for every
document type alike.
"""
from datetime import date

from ocr import ExtractionResult, PositionedWord

from invoice_extraction.extract_invoice import extract_invoice


def _word(text: str, x0: float, y0: float, page: int = 0, confidence: float = 1.0) -> PositionedWord:
    return PositionedWord(
        text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page, confidence=confidence,
    )


def _extraction_result(words: list[PositionedWord]) -> ExtractionResult:
    text = "\n".join(w.text for w in words)
    return ExtractionResult(
        text=text, confidence=0.9, method="ocr", pages=1,
        words_by_page=[words], page_dimensions=[(1000.0, 1400.0)],
    )


class TestCriticalFieldsAreDocumentTypeAware:
    def test_a_receipt_with_no_invoice_number_can_still_auto_process(self) -> None:
        """The real-dataset finding this exists to fix: invoice_number was
        found on 0/35 real informal receipts, which meant this alone forced
        needs_review_high_priority on every single one regardless of how
        good the rest of the extraction was."""
        words = [
            _word("Express", 50, 50), _word("Mart", 110, 50),
            _word("receipt", 50, 90),
            _word("Total", 50, 200), _word("270.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.document_type.value != "invoice"
        assert result.invoice_number.value is None
        assert result.review_status != "needs_review_high_priority"

    def test_a_document_confidently_typed_as_an_invoice_still_requires_its_number(self) -> None:
        """A formal invoice missing its own reference number is still a
        genuine risk signal, unlike an informal receipt — the stricter bar
        stays for this one document type."""
        words = [
            _word("Acme", 50, 50), _word("Corp", 110, 50),
            _word("invoice", 50, 90),
            _word("Total", 50, 200), _word("270.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.document_type.value == "invoice"
        assert result.invoice_number.value is None
        assert result.review_status == "needs_review_high_priority"
        assert "invoice_number_required_but_missing" in result.review_flags

    def test_missing_total_still_blocks_regardless_of_document_type(self) -> None:
        """total stays critical for every document type — this fix only
        ever relaxes the invoice_number requirement, nothing else."""
        words = [_word("Express", 50, 50), _word("Mart", 110, 50), _word("receipt", 50, 90)]
        result = extract_invoice(_extraction_result(words))
        assert result.review_status == "needs_review_high_priority"


class TestArithmeticOkReflectsEveryPlausibilityCheck:
    def test_an_implausible_date_blocks_auto_processing_even_with_critical_fields_present(self) -> None:
        """Regression guard for a real bug found live while testing the fix
        above: arithmetic_ok previously only ever looked at totals_pass, so
        a document with a flagged invoice_date_implausible could still
        reach auto_processed once critical fields were otherwise present —
        silently carrying a known-wrong date into the ledger."""
        words = [
            _word("Express", 50, 50), _word("Mart", 110, 50),
            _word("receipt", 50, 90),
            _word("Date", 50, 140), _word("01/01/2099", 150, 140),
            _word("Total", 50, 200), _word("270.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words), today=date(2026, 6, 1))
        assert result.arithmetic_validation.date_plausible is False
        assert "invoice_date_implausible" in result.review_flags
        assert result.review_status != "auto_processed"


class TestTotalFallbacksFromRealDocumentAudit:
    """Two more real bugs found live during a `no_total_found` root-cause
    audit against the same 35-receipt dataset — see docs/invoice-ocr-plan.md
    for the full document-by-document analysis."""

    def test_amount_inc_sales_tax_is_recognized_as_the_total_label(self) -> None:
        """Found live on real Pakistani POS receipts (KFC-branded): this is
        the document's own printed total line, with no separate "Grand
        Total"/"Total" label anywhere else on the page."""
        words = [
            _word("Express", 50, 50), _word("Mart", 110, 50),
            _word("receipt", 50, 90),
            _word("Amount", 50, 200), _word("Inc.", 110, 200),
            _word("Sales", 160, 200), _word("Tax", 220, 200), _word("270.00", 280, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 270.0
        assert result.total.method == "label_anchor+pattern"

    def test_a_glued_bill_total_label_is_recovered_via_dynamic_field_discovery(self) -> None:
        """Regression guard for a real bug found live on a real invoice:
        "BillTotal:" printed with no space at all — LABEL_VARIANTS can
        never match this as the word "total" (the cleaned word is
        "billtotal", not "total"), but the generic label/value discovery
        pass (dynamic_fields.py) already reads it correctly as its own
        label/value pair; _total_field just never consulted it. A second,
        unlabeled decimal-shaped figure elsewhere makes find_total_anywhere
        itself see two ambiguous candidates and correctly refuse — proving
        this is the dynamic-fields fallback resolving it, not the
        positional one."""
        words = [
            _word("Aeem", 50, 50), _word("Hardware", 110, 50),
            _word("invoice", 50, 90),
            _word("600.00", 50, 140),
            _word("BillTotal:", 50, 200), _word("880.00", 250, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 880.0
        assert result.total.method == "positional_fallback"

    def test_two_total_shaped_discovered_fields_is_ambiguous_and_stays_not_found(self) -> None:
        """The same conservative "exactly one candidate" standard
        find_total_anywhere holds itself to, applied to the dynamic-fields
        fallback too — two competing total-shaped labels must not resolve
        to a guess."""
        words = [
            _word("Aeem", 50, 50), _word("Hardware", 110, 50),
            _word("invoice", 50, 90),
            _word("BillTotal:", 50, 200), _word("880.00", 250, 200),
            _word("GrandTotal:", 50, 260), _word("920.00", 260, 260),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value is None


class TestP1BVendorEvidenceFlowsThroughExtractInvoice:
    """_vendor_field's own P1/P1-B evidence (ambiguous, low_confidence)
    end-to-end through the full extract_invoice() pipeline — not just at
    fields.py's own unit-test level."""

    def test_a_confident_unambiguous_vendor_is_reported_at_full_confidence(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Total", 50, 200), _word("500.00", 200, 200),
        ]
        er = _extraction_result(words)
        result = extract_invoice(er)
        assert result.vendor_name.value == "Riverside Traders"
        assert result.vendor_name.evidence["ambiguous"] is False
        assert result.vendor_name.evidence["low_confidence"] is False
        assert result.vendor_name.confidence == er.confidence

    def test_a_lone_sub_floor_vendor_candidate_is_flagged_low_confidence_and_discounted(self) -> None:
        """The real, live-observed failure shape (P1-B): a garbled corner
        stamp is the only thing OCR recovered in the header region, and the
        real vendor name was never extracted at all — the field still
        returns a value (recall preserved) but at a visibly discounted
        confidence, not the same full confidence a genuine clean read
        would get."""
        words = [
            _word("BiQ-13", 50, 50, confidence=0.6),
            _word("Total", 50, 200), _word("500.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.vendor_name.value == "BiQ-13"
        assert result.vendor_name.evidence["low_confidence"] is True
        base_conf = _extraction_result(words).confidence
        assert result.vendor_name.confidence == base_conf * 0.7


class TestP1CInvoiceDateEvidenceFlowsThroughExtractInvoice:
    """_date_field's own P1-C evidence (ambiguous, low_confidence,
    ambiguous_format) end-to-end through the full extract_invoice()
    pipeline — not just at fields.py's own unit-test level."""

    def test_a_confident_unambiguous_date_is_reported_at_full_confidence(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Invoice", 50, 90), _word("Date:", 130, 90), _word("10", 200, 90), _word("June", 230, 90), _word("2026", 280, 90),
            _word("Total", 50, 200), _word("500.00", 200, 200),
        ]
        er = _extraction_result(words)
        result = extract_invoice(er)
        assert result.invoice_date.value == date(2026, 6, 10)
        assert result.invoice_date.evidence["ambiguous"] is False
        assert result.invoice_date.evidence["low_confidence"] is False
        assert result.invoice_date.evidence["ambiguous_format"] is False
        assert result.invoice_date.confidence == er.confidence

    def test_invoice_date_wins_over_a_due_date_printed_first(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Due", 50, 90), _word("Date:", 100, 90), _word("10", 160, 90), _word("July", 190, 90), _word("2026", 230, 90),
            _word("Invoice", 50, 130), _word("Date:", 130, 130), _word("10", 200, 130), _word("June", 230, 130), _word("2026", 280, 130),
            _word("Total", 50, 200), _word("500.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.invoice_date.value == date(2026, 6, 10)

    def test_a_genuinely_ambiguous_numeric_date_is_flagged_and_discounted(self) -> None:
        """The real, live-observed failure shape (P1-C, docs/invoice-ocr-
        plan.md §17): a document's only date is a slash/dash numeric date
        where day-first and month-first both parse to a different,
        equally-plausible date. The value is still returned (recall
        preserved) but visibly discounted, not silently guessed at with
        full confidence."""
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Date:", 50, 90), _word("06/02/2026", 100, 90),
            _word("Total", 50, 200), _word("500.00", 200, 200),
        ]
        er = _extraction_result(words)
        result = extract_invoice(er)
        assert result.invoice_date.evidence["ambiguous_format"] is True
        assert result.invoice_date.confidence == er.confidence * 0.7


class TestP1DTotalEvidenceFlowsThroughExtractInvoice:
    """_total_field's own P1-D evidence (ambiguous, low_confidence) end-to-
    end through the full extract_invoice() pipeline, and — critically —
    proof that P0's own downstream safety checks (source-location
    collision, magnitude plausibility) still run unconditionally on
    whatever P1-D's candidate selection returns. Magnitude plausibility
    itself is already thoroughly covered end-to-end in
    test_total_plausibility.py; not duplicated here."""

    def test_a_confident_unambiguous_total_is_reported_at_full_confidence(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Grand", 50, 90), _word("Total:", 120, 90), _word("1000.00", 200, 90),
        ]
        er = _extraction_result(words)
        result = extract_invoice(er)
        assert result.total.value == 1000.0
        assert result.total.evidence["ambiguous"] is False
        assert result.total.evidence["low_confidence"] is False
        assert result.total.confidence == er.confidence

    def test_a_total_sharing_source_location_with_subtotal_still_routes_to_review(self) -> None:
        """The exact real bug shape P0-B's own source-collision check
        exists for (docs/invoice-ocr-plan.md §19) — reproduced end to end
        through the full pipeline (not only at arithmetic.
        shares_source_location's own pure-function level, test_arithmetic.
        py): a document whose only monetary value is itself labeled "Sub
        Total" still legitimately becomes both fields' answer from the
        identical source location — P1-D's own candidate architecture must
        never let that stop P0's own downstream check from firing."""
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Sub", 50, 90), _word("Total:", 100, 90), _word("900.00", 200, 90),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 900.0
        assert result.subtotal.value == 900.0
        assert result.total.page == result.subtotal.page
        assert result.total.bbox == result.subtotal.bbox
        assert "total_shares_source_with_another_field" in result.review_flags
        assert result.review_status != "auto_processed"

    def test_the_dynamic_total_fallback_receives_the_same_p0_safety_pipeline(self) -> None:
        """Phase 11's own explicit audit requirement: the dynamic-fields-
        discovery fallback (_dynamic_total_candidate) must never bypass
        P0's magnitude/source-collision checks. A dynamic-discovered total
        wildly inconsistent with the document's own subtotal+tax must still
        be routed to review, exactly like a label-anchored one would be."""
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("Subtotal", 50, 90), _word("2,500.00", 200, 90),
            _word("Tax", 50, 130), _word("250.00", 200, 130),
            _word("BillTotal:", 50, 170), _word("277,000.00", 250, 170),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 277000.0
        assert result.total.method == "positional_fallback"  # the dynamic tier's own method tag
        assert result.arithmetic_validation.magnitude_plausible is False
        assert "total_magnitude_implausible" in result.review_flags
        assert result.review_status != "auto_processed"


class TestNonTransactionalDocuments:
    """The end-to-end shape of non-transactional handling: an internal memo
    carries amounts but is not a purchase, so its amount must never occupy
    `total` — every spend aggregate, cashbook total and accounting hand-off
    downstream reads that field."""

    def _minute_sheet(self):
        return [
            _word("STIXOR", 50, 50), _word("MINUTE", 130, 50), _word("SHEET", 210, 50),
            _word("a.", 50, 120), _word("Bill-1(Legal)", 90, 120), _word("-Rs.125", 220, 120),
            _word("b.", 50, 170), _word("Bill-2(Emp", 90, 170), _word("Care)", 190, 170), _word("-Rs.10,200", 260, 170),
            _word("For", 50, 240), _word("approval", 90, 240), _word("of", 180, 240),
            _word("Rs.", 210, 240), _word("22,875/-", 250, 240), _word("by", 330, 240), _word("CEO,", 360, 240),
        ]

    def test_a_minute_sheet_is_detected_and_marked_non_transactional(self) -> None:
        result = extract_invoice(_extraction_result(self._minute_sheet()))
        assert result.document_type.value == "minute_sheet"
        assert result.transactional is False

    def test_the_stated_amount_becomes_amount_mentioned_never_the_total(self) -> None:
        """The single most important rule in this feature."""
        result = extract_invoice(_extraction_result(self._minute_sheet()))
        assert result.amount_mentioned == 22875.0
        assert result.total.value is None
        assert result.subtotal.value is None
        assert result.tax_amount.value is None

    def test_vendor_name_is_cleared_not_left_as_the_memos_own_letterhead(self) -> None:
        """Regression guard for a real false positive found live via the
        golden-dataset benchmark (docs/invoice-ocr-plan.md §15): all 4
        confirmed non-transactional documents in the real 35-document
        dataset correctly cleared total/subtotal/tax_amount but still
        reported a "vendor" — the positional fallback has no notion that a
        memo has no vendor at all, and returns whatever prominent text
        sits at the top of the page (here, the issuing company's own
        letterhead, "STIXOR MINUTE SHEET"). Nothing downstream — category
        assignment, a vendor-spend aggregate, the cashbook — should ever
        read an internal memo's own letterhead as if it were a business
        the company purchased from."""
        result = extract_invoice(_extraction_result(self._minute_sheet()))
        assert result.vendor_name.value is None

    def test_it_is_flagged_and_routed_to_ordinary_review_not_high_priority(self) -> None:
        """Nothing is broken on this document — it simply isn't a
        transaction — so escalating it as though extraction failed would be
        wrong. A person still confirms the call, so it is never
        auto-processed either."""
        result = extract_invoice(_extraction_result(self._minute_sheet()))
        assert "non_transactional_document" in result.review_flags
        assert "no_total_found" not in result.review_flags
        assert result.review_status == "needs_review"

    def test_the_classification_reason_is_the_documents_own_wording(self) -> None:
        result = extract_invoice(_extraction_result(self._minute_sheet()))
        assert result.classification_reason is not None
        assert "MINUTE SHEET" in result.classification_reason

    def test_a_normal_invoice_keeps_its_total_and_stays_transactional(self) -> None:
        words = [
            _word("Acme", 50, 50), _word("Traders", 110, 50),
            _word("TAX", 50, 90), _word("INVOICE", 90, 90),
            _word("Total", 50, 200), _word("5,000.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.transactional is True
        assert result.total.value == 5000.0
        assert result.amount_mentioned is None
        assert "non_transactional_document" not in result.review_flags
        # A real, legitimate vendor on a genuine transaction must never be
        # touched by the non-transactional clearing rule — the fix this
        # class is named for only ever fires when transactional is False.
        assert result.vendor_name.value == "Acme Traders"

    def test_a_quotation_is_marked_non_transactional_but_keeps_its_own_fields(self) -> None:
        """P0-A (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md
        P0-1): unlike a minute sheet/approval request, a quotation names a
        real external vendor and carries a real quoted total — both are
        genuine information about the document, never cleared. Only
        `transactional=False` keeps it out of the cashbook, the same
        mechanism invoice-service's own transaction_status filtering
        already uses for every non-transactional type."""
        words = [
            _word("Northwind", 50, 50), _word("Traders", 150, 50),
            _word("Quotation", 50, 90),
            _word("Total", 50, 200), _word("15,000.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.document_type.value == "quotation"
        assert result.transactional is False
        assert result.vendor_name.value == "Northwind Traders"
        assert result.total.value == 15000.0
        assert result.amount_mentioned is None

    def test_a_purchase_order_is_marked_non_transactional_but_keeps_its_own_fields(self) -> None:
        words = [
            _word("Acme", 50, 50), _word("Supplies", 140, 50),
            _word("Purchase", 50, 90), _word("Order", 130, 90), _word("#445", 190, 90),
            _word("Total", 50, 200), _word("8,000.00", 200, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.document_type.value == "purchase_order"
        assert result.transactional is False
        assert result.vendor_name.value == "Acme Supplies"
        assert result.total.value == 8000.0
        assert result.amount_mentioned is None

    def test_quotation_and_purchase_order_never_auto_process(self) -> None:
        """Diverting a document out of the cashbook is always a decision a
        person confirms — same TXN-003 carve-out a minute sheet already
        gets, applied here for the same reason."""
        for keyword in ("Quotation", "Purchase Order"):
            keyword_words = [_word(w, 50 + i * 90, 90) for i, w in enumerate(keyword.split())]
            words = [
                _word("Some", 50, 50), _word("Vendor", 120, 50),
                *keyword_words,
                _word("Total", 50, 200), _word("5,000.00", 200, 200),
            ]
            result = extract_invoice(_extraction_result(words))
            assert result.review_status == "needs_review", keyword
            assert "non_transactional_document" in result.review_flags, keyword

    def test_vendor_clearing_is_not_tied_to_any_specific_company_or_wording(self) -> None:
        """General-pattern guard, deliberately using a fictitious company
        unrelated to the real dataset — the vendor-clearing rule fires on
        `transactional is False` alone, never on any particular vendor
        name, wording, or document shape."""
        words = [
            _word("Northwind", 50, 50), _word("Traders", 150, 50), _word("APPROVAL", 260, 50),
            _word("Subject:", 50, 90), _word("Office", 120, 90), _word("Supplies", 190, 90),
            _word("For", 50, 150), _word("approval", 90, 150), _word("of", 180, 150),
            _word("Rs.", 210, 150), _word("4,500/-", 250, 150), _word("by", 330, 150), _word("CEO,", 360, 150),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.transactional is False
        assert result.vendor_name.value is None
        assert result.total.value is None
        assert result.amount_mentioned == 4500.0
