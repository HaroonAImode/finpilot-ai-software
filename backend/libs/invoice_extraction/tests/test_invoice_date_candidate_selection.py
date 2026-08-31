"""P1-C — invoice_date's own application of the shared candidate-selection
architecture (candidates.py), mirroring test_vendor_candidate_selection.py's
approach for vendor. Every case here is synthetic and general — none of the
35 real Golden Dataset documents' own filenames or dates are hardcoded.

test_fields.py's TestFindDateAnywhere still covers the older, independent
find_date_anywhere function (kept, unmodified, still used by nothing in
extract_invoice.py — see its own docstring); this file covers what's
actually new: find_invoice_date_candidate_selection's full document-wide
multi-candidate scoring, ambiguity handling, and the due-date/other-date
negative evidence.
"""
from datetime import date

from ocr import PositionedWord

from invoice_extraction.fields import date_candidate_cleared_confidence_floor, find_invoice_date_candidate_selection
from invoice_extraction.lines import group_into_lines


def _word(text: str, x0: float, y0: float, page: int = 0, confidence: float = 1.0) -> PositionedWord:
    return PositionedWord(
        text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page, confidence=confidence,
    )


class TestNormalLabeledDate:
    def test_a_clean_labeled_invoice_date_is_a_confident_accept(self) -> None:
        words = [
            _word("Invoice", 50, 50), _word("Date:", 130, 50), _word("10", 200, 50), _word("June", 230, 50), _word("2026", 280, 50),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner is not None
        assert result.winner.value == "10 June 2026"
        assert result.status == "accept"
        assert result.winner.method == "label_anchor+pattern"

    def test_a_bare_date_label_is_weaker_but_still_wins_alone(self) -> None:
        words = [_word("Date:", 50, 50), _word("10", 120, 50), _word("June", 150, 50), _word("2026", 200, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "10 June 2026"
        assert result.status == "accept"


class TestUnlabeledPositionalDate:
    def test_a_bare_date_with_no_label_anywhere_is_found(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50),
            _word("16", 50, 100, confidence=0.92), _word("Jun", 90, 100, confidence=0.92), _word("2026", 130, 100, confidence=0.92),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "16 Jun 2026"
        assert result.winner.method == "positional_fallback"

    def test_a_low_confidence_lone_date_still_wins_but_is_flagged(self) -> None:
        words = [_word("12/06/2026", 50, 50, confidence=0.4)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner is not None  # recall preserved — still names a winner
        assert date_candidate_cleared_confidence_floor(result.winner) is False

    def test_no_date_anywhere_is_unknown(self) -> None:
        words = [_word("Total", 50, 50), _word("400.00", 150, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.status == "unknown"
        assert result.winner is None


class TestOCRNoiseNormalizationStillWorks:
    """Regression guard: §17's ordinal-suffix/missing-separator fixes
    (validators.py) must survive being routed through the new candidate
    architecture unchanged."""

    def test_ordinal_suffix_noise_is_still_recovered(self) -> None:
        words = [_word('30"', 50, 50), _word("June", 90, 50), _word("2026", 150, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == '30" June 2026'

    def test_missing_separator_is_still_recovered(self) -> None:
        words = [_word("17Jun", 50, 50), _word("2026", 100, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "17Jun 2026"


class TestInvoiceDateVsDueDate:
    """Phase 6's own explicit requirement: the system must not simply
    select the first valid date — an invoice-date-labeled value must win
    over a due-date-labeled one regardless of which prints first."""

    def test_invoice_date_wins_when_due_date_appears_after_it(self) -> None:
        words = [
            _word("Invoice", 50, 50), _word("Date:", 130, 50), _word("10", 200, 50), _word("June", 230, 50), _word("2026", 280, 50),
            _word("Due", 50, 90), _word("Date:", 100, 90), _word("10", 160, 90), _word("July", 190, 90), _word("2026", 230, 90),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "10 June 2026"

    def test_invoice_date_still_wins_when_due_date_appears_first(self) -> None:
        """The critical case: text order must not decide this — evidence
        must. A due-date label sitting physically above the real invoice
        date must not win merely because it comes first."""
        words = [
            _word("Due", 50, 50), _word("Date:", 100, 50), _word("10", 160, 50), _word("July", 190, 50), _word("2026", 230, 50),
            _word("Invoice", 50, 90), _word("Date:", 130, 90), _word("10", 200, 90), _word("June", 230, 90), _word("2026", 280, 90),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "10 June 2026"

    def test_a_due_date_line_alone_still_wins_when_it_is_the_only_date_in_the_document(self) -> None:
        """The same 'return something rather than nothing' recall
        guarantee vendor's own rejected-candidate tier preserves."""
        words = [_word("Due", 50, 50), _word("Date:", 100, 50), _word("10", 160, 50), _word("July", 190, 50), _word("2026", 230, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner is not None
        assert result.winner.value == "10 July 2026"

    def test_the_bare_date_token_inside_a_due_date_label_does_not_falsely_anchor(self) -> None:
        """The structural bug this phase closes: LABEL_VARIANTS'
        invoice_date list includes the bare word "date", which — without
        this check — would match the second word of "Due Date:" as if it
        were its own label, silently returning the due date as the invoice
        date on any document with no other invoice-date label at all."""
        words = [_word("Due", 50, 50), _word("Date:", 100, 50), _word("10", 160, 50), _word("July", 190, 50), _word("2026", 230, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.method == "positional_fallback" or "non_invoice_date_label" in result.winner.evidence


class TestMultipleUnrelatedDateTypes:
    def test_invoice_date_wins_among_order_delivery_and_due_dates(self) -> None:
        words = [
            _word("Order", 50, 50), _word("Date:", 110, 50), _word("1", 170, 50), _word("June", 190, 50), _word("2026", 240, 50),
            _word("Delivery", 50, 90), _word("Date:", 130, 90), _word("5", 190, 90), _word("June", 210, 90), _word("2026", 260, 90),
            _word("Invoice", 50, 130), _word("Date:", 130, 130), _word("10", 200, 130), _word("June", 230, 130), _word("2026", 280, 130),
            _word("Due", 50, 170), _word("Date:", 100, 170), _word("10", 160, 170), _word("July", 190, 170), _word("2026", 230, 170),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "10 June 2026"


class TestDayMonthAmbiguity:
    """Phase 7's own explicit requirement: never silently guess when the
    evidence cannot determine day/month order."""

    def test_an_ambiguous_numeric_date_is_flagged_in_evidence(self) -> None:
        words = [_word("Date:", 50, 50), _word("06/02/2026", 100, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner is not None  # still returns a value — recall preserved
        assert "day_month_ambiguous" in result.winner.evidence

    def test_an_unambiguous_numeric_date_is_not_flagged(self) -> None:
        """Day 30 cannot be a month — no genuine ambiguity exists here."""
        words = [_word("Date:", 50, 50), _word("30/06/2026", 100, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert "day_month_ambiguous" not in result.winner.evidence

    def test_a_textual_month_name_date_is_never_flagged_as_ambiguous(self) -> None:
        words = [_word("Date:", 50, 50), _word("10", 100, 50), _word("June", 130, 50), _word("2026", 180, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert "day_month_ambiguous" not in result.winner.evidence

    def test_an_unambiguous_clearer_candidate_beats_an_ambiguous_one(self) -> None:
        """When both a genuinely ambiguous and a genuinely clear invoice-
        date-labeled candidate exist, the clear one must win — ambiguity is
        evidence, not just a downstream flag."""
        words = [
            _word("Order", 50, 50), _word("Date:", 110, 50), _word("06/02/2026", 170, 50),
            _word("Invoice", 50, 90), _word("Date:", 130, 90), _word("10", 200, 90), _word("June", 230, 90), _word("2026", 280, 90),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.value == "10 June 2026"


class TestInvalidDatesAreNeverCandidates:
    def test_an_impossible_calendar_date_never_becomes_a_candidate(self) -> None:
        words = [_word("Date:", 50, 50), _word("31/02/2026", 100, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.status == "unknown"

    def test_nonsense_digits_never_become_a_candidate(self) -> None:
        words = [_word("Date:", 50, 50), _word("99/99/2026", 100, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.status == "unknown"


class TestAmbiguityMargin:
    def test_two_near_identical_weak_candidates_are_flagged_for_review(self) -> None:
        words = [
            _word("10/05/2026", 50, 50, confidence=0.60),
            _word("11/05/2026", 50, 100, confidence=0.601),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.status == "review"
        assert result.winner is not None  # a winner is still named

    def test_a_clear_confidence_gap_is_never_flagged_as_ambiguous(self) -> None:
        words = [
            _word("10/05/2026", 50, 50, confidence=0.6),
            _word("11/05/2026", 50, 100, confidence=0.95),
        ]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.status == "accept"


class TestProvenanceIsExplainable:
    def test_the_winning_candidates_own_evidence_sums_to_its_score(self) -> None:
        words = [_word("Invoice", 50, 50), _word("Date:", 130, 50), _word("10", 200, 50), _word("June", 230, 50), _word("2026", 280, 50)]
        lines = group_into_lines(words)
        result = find_invoice_date_candidate_selection(lines)
        assert result.winner.score == sum(result.winner.evidence.values())
        assert result.winner.page == 0
        assert result.winner.bbox is not None
