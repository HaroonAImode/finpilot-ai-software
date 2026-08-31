"""P1-D — total's own application of the shared candidate-selection
architecture (candidates.py), mirroring test_vendor_candidate_selection.py
and test_invoice_date_candidate_selection.py's approach. Every case here is
synthetic and general — none of the 35 real Golden Dataset documents' own
filenames or figures are hardcoded.

find_total_anywhere (fields.py) is unmodified and still independently
tested elsewhere in test_fields.py; this file covers what's actually new:
find_total_candidate_selection's full document-wide multi-candidate
scoring, the subtotal/tax/discount/cash/change negative evidence, the
Amount-Due/Balance-Due contextual tier, and arithmetic-consistency evidence.
"""
from ocr import PositionedWord

from invoice_extraction.fields import find_total_candidate_selection, total_candidate_cleared_confidence_floor
from invoice_extraction.lines import group_into_lines


def _word(text: str, x0: float, y0: float, page: int = 0, confidence: float = 1.0) -> PositionedWord:
    return PositionedWord(
        text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page, confidence=confidence,
    )


class TestBasicExplicitTotal:
    def test_a_clean_grand_total_is_a_confident_accept(self) -> None:
        words = [_word("Grand", 50, 50), _word("Total:", 110, 50), _word("1,250.00", 180, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "1,250.00"
        assert result.status == "accept"
        assert result.winner.method == "label_anchor+pattern"

    def test_a_bare_total_label_is_weaker_but_still_wins_alone(self) -> None:
        words = [_word("Total:", 50, 50), _word("500.00", 120, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "500.00"
        assert result.status == "accept"


class TestSubtotalTaxDiscountDoNotWinOverTotal:
    """The real, confirmed bug this phase closes: LABEL_VARIANTS["total"]'s
    own bare "total" token matches the second word of "Sub Total:"/"Total
    Tax:"/"Total Discount:" lines. The old single-shot label lookup
    returned on the first line matching *any* variant, so a subtotal/tax/
    discount line appearing before the real total could win outright."""

    def test_subtotal_does_not_win_over_a_grand_total_printed_after_it(self) -> None:
        words = [
            _word("Sub", 50, 50), _word("Total:", 100, 50), _word("900.00", 200, 50),
            _word("Grand", 50, 90), _word("Total:", 120, 90), _word("1000.00", 220, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "1000.00"

    def test_a_total_tax_line_does_not_win_over_the_real_total(self) -> None:
        words = [
            _word("Total", 50, 50), _word("Tax:", 110, 50), _word("50.00", 170, 50),
            _word("Grand", 50, 90), _word("Total:", 120, 90), _word("1000.00", 220, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "1000.00"

    def test_a_total_discount_line_does_not_win_over_the_real_total(self) -> None:
        words = [
            _word("Total", 50, 50), _word("Discount:", 110, 50), _word("20.00", 190, 50),
            _word("Grand", 50, 90), _word("Total:", 120, 90), _word("1000.00", 220, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "1000.00"

    def test_amount_inc_sales_tax_is_not_mistaken_for_a_different_field(self) -> None:
        """Regression guard for a bug introduced and self-corrected during
        this phase's own implementation: "Amount Inc. Sales Tax" (a real,
        strong total label) itself contains the word "tax" — the negative-
        evidence check must not self-penalize the very label that makes
        this candidate legitimate."""
        words = [_word("Amount", 50, 50), _word("Inc.", 110, 50), _word("Sales", 160, 50), _word("Tax", 220, 50), _word("270.00", 280, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "270.00"
        assert "non_total_field_label" not in result.winner.evidence

    def test_a_subtotal_alone_still_wins_when_it_is_the_only_amount_in_the_document(self) -> None:
        """The same 'return something rather than nothing' recall
        guarantee vendor's own rejected-candidate tier preserves."""
        words = [_word("Sub", 50, 50), _word("Total:", 100, 50), _word("900.00", 200, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner is not None
        assert result.winner.value == "900.00"


class TestBillTotalIsAStrongLabel:
    """P1-D Golden Dataset error analysis: "Bill Total: 900.00" only
    matched the bare "total" token before this, tying it with a garbled
    same-line value bled from an adjacent quantity column ("Total: 3
    900.00") in the same weak tier — a razor-thin, document-order-only
    margin then let the garbled one win. "Bill total" is the same general,
    template-agnostic "[qualifier] total" vocabulary "grand total"/"total
    amount" already cover."""

    def test_bill_total_wins_over_a_garbled_bare_total_line(self) -> None:
        words = [
            _word("Total:", 50, 50), _word("3", 110, 50), _word("900.00", 140, 50),
            _word("Bill", 50, 90), _word("Total:", 100, 90), _word("900.00", 160, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "900.00"
        assert result.status == "accept"


class TestMultipleMonetaryValues:
    def test_grand_total_wins_among_subtotal_and_tax(self) -> None:
        words = [
            _word("Subtotal", 50, 50), _word("800.00", 150, 50),
            _word("Tax", 50, 90), _word("80.00", 150, 90),
            _word("Grand", 50, 130), _word("Total:", 110, 130), _word("880.00", 180, 130),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "880.00"


class TestAmountDueVsInvoiceTotal:
    """Phase 7's own explicit requirement: Amount Due/Balance Due are
    weaker, contextual evidence — never automatically authoritative, but a
    legitimate total when nothing stronger exists or when arithmetic
    confirms it."""

    def test_invoice_total_wins_over_a_partial_payment_balance_due(self) -> None:
        words = [
            _word("Invoice", 50, 50), _word("Total:", 120, 50), _word("1000.00", 190, 50),
            _word("Amount", 50, 90), _word("Paid:", 120, 90), _word("400.00", 180, 90),
            _word("Balance", 50, 130), _word("Due:", 130, 130), _word("600.00", 180, 130),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.value == "1000.00"

    def test_amount_due_alone_is_a_valid_total_candidate(self) -> None:
        words = [_word("Amount", 50, 50), _word("Due:", 120, 50), _word("1000.00", 180, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner is not None
        assert result.winner.value == "1000.00"
        assert total_candidate_cleared_confidence_floor(result.winner) is False

    def test_amount_due_confirmed_by_arithmetic_clears_the_confidence_floor(self) -> None:
        words = [
            _word("Subtotal", 50, 50), _word("900.00", 150, 50),
            _word("Tax", 50, 90), _word("100.00", 150, 90),
            _word("Amount", 50, 130), _word("Due:", 120, 130), _word("1000.00", 180, 130),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines, subtotal=900.0, tax=100.0)
        assert result.winner.value == "1000.00"
        assert total_candidate_cleared_confidence_floor(result.winner) is True


class TestArithmeticConsistencyEvidence:
    def test_a_candidate_reconciling_with_subtotal_plus_tax_is_favored(self) -> None:
        words = [
            _word("Subtotal", 50, 50), _word("900.00", 150, 50),
            _word("Tax", 50, 90), _word("100.00", 150, 90),
            _word("Total:", 50, 130), _word("1000.00", 150, 130),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines, subtotal=900.0, tax=100.0)
        assert "candidate_reconciles_with_reference" in result.winner.evidence

    def test_arithmetic_inconsistency_alone_does_not_reject_a_candidate(self) -> None:
        """Arithmetic evidence is a positive bonus when present, never a
        penalty when absent — a candidate that doesn't reconcile is not
        automatically wrong (a real discount/rounding/unusual invoice can
        legitimately not match), just without that extra support."""
        words = [_word("Total:", 50, 50), _word("1000.00", 120, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines, subtotal=500.0, tax=0.0)
        assert result.winner is not None
        assert result.winner.value == "1000.00"
        assert "candidate_reconciles_with_reference" not in result.winner.evidence


class TestLineItemBodyIsExcluded:
    def test_a_value_inside_the_line_item_table_body_does_not_win_over_the_real_total(self) -> None:
        words = [
            _word("Widget", 50, 50), _word("Total", 150, 50), _word("117600", 250, 50),  # a line-item description that happens to contain "Total"
            _word("Grand", 50, 90), _word("Total:", 120, 90), _word("183254", 200, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines, item_body=range(0, 1))
        assert result.winner.value == "183254"


class TestAmbiguityMargin:
    def test_two_near_identical_weak_candidates_are_flagged_for_review(self) -> None:
        words = [
            _word("Amount", 50, 50), _word("Due:", 120, 50), _word("500.00", 180, 50),
            _word("Balance", 50, 90), _word("Due:", 130, 90), _word("501.00", 190, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.status == "review"
        assert result.winner is not None  # a winner is still named

    def test_a_clear_tier_gap_is_never_flagged_as_ambiguous(self) -> None:
        words = [
            _word("Grand", 50, 50), _word("Total:", 110, 50), _word("1000.00", 180, 50),
            _word("Amount", 50, 90), _word("Due:", 120, 90), _word("1000.00", 180, 90),
        ]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.status == "accept"
        assert result.winner.value == "1000.00" and result.winner.method == "label_anchor+pattern"


class TestNoTotalIsUnknown:
    def test_no_monetary_value_anywhere_is_unknown(self) -> None:
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.status == "unknown"
        assert result.winner is None


class TestProvenanceIsExplainable:
    def test_the_winning_candidates_own_evidence_sums_to_its_score(self) -> None:
        words = [_word("Grand", 50, 50), _word("Total:", 110, 50), _word("1000.00", 180, 50)]
        lines = group_into_lines(words)
        result = find_total_candidate_selection(lines)
        assert result.winner.score == sum(result.winner.evidence.values())
