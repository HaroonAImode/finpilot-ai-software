from invoice_extraction.document_type import detect_document_type, is_transactional


class TestKeywordEvidence:
    def test_invoice_keyword_wins(self) -> None:
        result = detect_document_type("TAX INVOICE\nVendor: ABC", has_line_items=True, has_total=True)
        assert result.document_type == "invoice"
        assert result.confidence == 1.0

    def test_receipt_keyword_wins(self) -> None:
        result = detect_document_type("Cash Receipt\nVendor: ABC", has_line_items=False, has_total=True)
        assert result.document_type == "receipt"

    def test_bill_keyword_wins(self) -> None:
        result = detect_document_type("Electricity Bill\nAmount: 500", has_line_items=False, has_total=True)
        assert result.document_type == "bill"

    def test_credit_note_keyword_wins_over_generic_words(self) -> None:
        result = detect_document_type("CREDIT NOTE\nInvoice ref: 123", has_line_items=False, has_total=False)
        assert result.document_type == "credit_note"

    def test_purchase_order_keyword_is_recognized(self) -> None:
        result = detect_document_type("Purchase Order #445", has_line_items=True, has_total=False)
        assert result.document_type == "purchase_order"


class TestNoKeywordEvidence:
    def test_a_line_item_table_with_a_total_is_a_weak_receipt_guess(self) -> None:
        result = detect_document_type(
            "YZ Paint & Hardware\nQty Particulars Rate Amount\n1 Hathori Small 350 350",
            has_line_items=True, has_total=True,
        )
        assert result.document_type == "receipt"
        assert result.confidence < 1.0  # a guess, not a confident classification

    def test_no_keyword_and_no_recognizable_shape_is_unknown_not_a_guess(self) -> None:
        result = detect_document_type("Some unrelated document text", has_line_items=False, has_total=False)
        assert result.document_type is None
        assert result.confidence == 0.0
        assert result.reason is None


class TestInternalPaperworkIsNotAPurchaseDocument:
    """Real documents from a 35-document set: internal expense-approval
    memos that carry amounts but are not purchases. Before this, all four
    were typed "bill" at confidence 1.0 — confidently wrong — because they
    itemise their attachments as "Bill-1(Legal)", "Bill-2(Emp Care)"."""

    def test_a_minute_sheet_is_not_typed_as_a_bill_by_its_own_line_items(self) -> None:
        text = (
            "STIXOR\nMINUTE SHEET\n"
            "Subject: Repair & Maintenance / Stationary\n"
            "a. Bill -1(Legal) -Rs.125\n"
            "b. Bill-2(Emp Care) - Rs.10,200\n"
            "For approval of Rs. 22,875/- (Twenty two thousand eight hundred seventy five only) by CEO, please."
        )
        result = detect_document_type(text, has_line_items=False, has_total=False)
        assert result.document_type == "minute_sheet"
        assert result.confidence == 1.0
        assert is_transactional(result.document_type) is False

    def test_approval_wording_alone_classifies_an_approval_request(self) -> None:
        text = "Office memo\nFor approval of Rs. 3,180/- (three thousand one eighty only) by CEO and COO, please."
        result = detect_document_type(text, has_line_items=False, has_total=False)
        assert result.document_type == "approval_request"
        assert is_transactional(result.document_type) is False

    def test_the_reason_quotes_the_documents_own_wording(self) -> None:
        """The review UI shows this verbatim so a person can judge the call
        themselves rather than trust an opaque label."""
        text = "Some memo\nFor approval of Rs. 22,875/- by CEO, please."
        result = detect_document_type(text, has_line_items=False, has_total=False)
        assert result.reason is not None
        assert "For approval of Rs. 22,875" in result.reason

    def test_a_normal_invoice_stays_transactional(self) -> None:
        result = detect_document_type("TAX INVOICE\nTotal 5000", has_line_items=True, has_total=True)
        assert result.document_type == "invoice"
        assert is_transactional(result.document_type) is True

    def test_a_normal_receipt_stays_transactional(self) -> None:
        result = detect_document_type("Cash Receipt\nGrand Total 270", has_line_items=True, has_total=True)
        assert result.document_type == "receipt"
        assert is_transactional(result.document_type) is True

    def test_an_invoice_mentioning_approved_in_its_footer_is_still_an_invoice(self) -> None:
        """Only phrasings that frame the *document* as a request for
        approval count — a payment-terms footer saying "approved" must not
        divert a real invoice out of financial processing."""
        text = "TAX INVOICE\nTotal 5000\nPayment approved by accounts on receipt."
        result = detect_document_type(text, has_line_items=True, has_total=True)
        assert result.document_type == "invoice"
        assert is_transactional(result.document_type) is True

    def test_an_undetected_type_defaults_to_transactional(self) -> None:
        """"We could not tell" must not silently divert a real invoice into
        a bucket nobody looks at — the confidence system already routes it
        to review."""
        assert is_transactional(None) is True
        assert is_transactional("unknown") is True


class TestQuotationAndPurchaseOrderAreNotTransactions:
    """P0-A (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md
    P0-1): a quotation is a proposed price and a purchase order is an
    intent to buy — neither represents money that has actually changed
    hands, so neither may enter financial transaction processing, exactly
    like an internal minute sheet or approval request. Unlike those two,
    extract_invoice.py never clears a quotation/PO's own fields (see
    test_extract_invoice_routing.py) — this class only covers the
    classification boundary itself."""

    def test_a_quotation_is_not_transactional(self) -> None:
        assert is_transactional("quotation") is False

    def test_a_purchase_order_is_not_transactional(self) -> None:
        assert is_transactional("purchase_order") is False

    def test_every_other_recognized_type_is_unaffected(self) -> None:
        """Regression guard — this fix only ever adds two new members to
        NON_TRANSACTIONAL_TYPES, it must not change anything else."""
        assert is_transactional("invoice") is True
        assert is_transactional("receipt") is True
        assert is_transactional("bill") is True
        assert is_transactional("credit_note") is True
        assert is_transactional("minute_sheet") is False
        assert is_transactional("approval_request") is False
