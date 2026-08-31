from invoice_extraction.payment import detect_payment_status


class TestEvidenceBasedStatus:
    def test_paid_is_detected(self) -> None:
        assert detect_payment_status("Status: PAID").value == "PAID"

    def test_paid_in_full_is_detected_as_paid(self) -> None:
        assert detect_payment_status("Paid in full on 2026-01-01").value == "PAID"

    def test_unpaid_is_detected(self) -> None:
        assert detect_payment_status("Invoice status: UNPAID").value == "UNPAID"

    def test_partially_paid_is_detected_and_not_confused_with_paid(self) -> None:
        assert detect_payment_status("Partially paid: 200 of 500").value == "PARTIALLY_PAID"

    def test_balance_due_is_detected_as_due(self) -> None:
        assert detect_payment_status("Balance Due: 500").value == "DUE"

    def test_pending_is_detected(self) -> None:
        assert detect_payment_status("Payment Pending").value == "PENDING"


class TestNoEvidence:
    def test_no_payment_mention_returns_not_found_never_pending(self) -> None:
        """The exact bug this module exists to avoid: a separate reference
        project's normalization defaulted absent payment status to PENDING.
        Absence of evidence must stay not_found (UNKNOWN), not an assumed
        state."""
        result = detect_payment_status("Vendor: ABC Traders\nItem: Widget\nTotal: 350")
        assert result.value is None
        assert result.method == "not_found"
