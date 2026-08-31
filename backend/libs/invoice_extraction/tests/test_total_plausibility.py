"""P0-B end-to-end coverage — the magnitude/plausibility validation layer
(docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md's P0-2/P0-3
findings), exercised through the real extract_invoice() pipeline rather
than only the arithmetic.py helper functions in isolation (see
test_arithmetic.py for those).

Every case below is synthetic and general on purpose — the point is to
prove the *behavior class* ("a structurally valid but magnitude-implausible
total must not silently reach auto_processed"), never to special-case one
real Golden Dataset document. No filename, vendor, or exact figure from the
real 35-document dataset is reused here.
"""
from invoice_extraction.extract_invoice import extract_invoice
from ocr import ExtractionResult, PositionedWord


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


class TestCaseA_NormalDocumentIsAccepted:
    """Subtotal 2,500 + Tax 250 = Total 2,750 — everything agrees."""

    def test_the_total_is_accepted_with_no_plausibility_flags(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("receipt", 50, 90),
            _word("Subtotal", 50, 150), _word("2,500.00", 300, 150),
            _word("Tax", 50, 190), _word("250.00", 300, 190),
            _word("Total", 50, 230), _word("2,750.00", 300, 230),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 2750.0
        assert result.arithmetic_validation.totals_pass is True
        assert result.arithmetic_validation.magnitude_plausible is True
        assert "total_magnitude_implausible" not in result.review_flags
        assert "total_shares_source_with_another_field" not in result.review_flags
        # P1-D: total.evidence now also carries _total_field's own
        # candidate-level evidence (ambiguous/low_confidence/total_evidence/
        # document_order/candidate_reconciles_with_reference) alongside
        # these three P0-B keys — checked as a subset, not exact equality,
        # since P0-B's own evidence is merged into (not replacing) it.
        assert result.total.evidence.items() >= {
            "arithmetic_consistent": True, "within_document_magnitude": True,
            "shares_source_with_another_field": False,
        }.items()


class TestCaseB_TheDangerousFailureClass:
    """The general shape of the real, live failure this whole layer exists
    for: a candidate total two orders of magnitude away from what the
    document's own subtotal+tax say it should be. The requirement is NOT
    that 277,000 becomes 2,750 — total is never silently rewritten — only
    that it can no longer reach auto_processed or, downstream, Saved
    Records' cashbook totals without a human confirming it first."""

    def test_the_implausible_total_is_kept_visible_but_flagged_not_silently_accepted(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("receipt", 50, 90),
            _word("Subtotal", 50, 150), _word("2,500.00", 300, 150),
            _word("Tax", 50, 190), _word("250.00", 300, 190),
            _word("Total", 50, 230), _word("277,000.00", 300, 230),
        ]
        result = extract_invoice(_extraction_result(words))
        # Never silently repaired — the exact extracted digits are still
        # the reported value, for a human to see and correct or confirm.
        assert result.total.value == 277000.0
        assert result.arithmetic_validation.totals_pass is False
        assert result.arithmetic_validation.magnitude_plausible is False
        assert "total_magnitude_implausible" in result.review_flags
        assert result.review_status != "auto_processed"
        # Field-level confidence is degraded too, distinct from — and in
        # addition to — the document-level routing effect.
        assert result.total.confidence < 0.5


class TestCaseC_And_H_LargeButConsistentInvoicesAreNeverRejected:
    """The explicit anti-goal of this whole layer: it must never behave
    like a fixed global ceiling. A large invoice that agrees with its own
    subtotal+tax is exactly as plausible as a small one."""

    def test_a_quarter_million_invoice_that_reconciles_is_accepted(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("invoice", 50, 90),
            _word("Subtotal", 50, 150), _word("250,000.00", 300, 150),
            _word("Tax", 50, 190), _word("25,000.00", 300, 190),
            _word("Total", 50, 230), _word("275,000.00", 300, 230),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 275000.0
        assert result.arithmetic_validation.magnitude_plausible is True
        assert "total_magnitude_implausible" not in result.review_flags

    def test_a_near_million_invoice_that_reconciles_can_still_auto_process(self) -> None:
        """A formal invoice also needs its own reference number to clear
        the critical-field bar (an unrelated, pre-existing requirement,
        included here so this test isn't accidentally passing for the
        wrong reason) — the point being verified is specifically that
        *magnitude* is never what blocks a large-but-consistent invoice."""
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("invoice", 50, 90),
            _word("Invoice", 50, 110), _word("No.", 130, 110), _word("INV-2026-001", 180, 110),
            _word("Subtotal", 50, 150), _word("900,000.00", 300, 150),
            _word("Tax", 50, 190), _word("90,000.00", 300, 190),
            _word("Total", 50, 230), _word("990,000.00", 300, 230),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 990000.0
        assert result.arithmetic_validation.magnitude_plausible is True
        assert result.arithmetic_validation.totals_pass is True
        assert "total_magnitude_implausible" not in result.review_flags
        assert result.review_status == "auto_processed"


class TestCaseD_AmbiguityBehaviorIsUnchanged:
    """Two unlabeled amount-shaped candidates with no total label at all —
    find_total_anywhere's existing conservative "exactly one candidate"
    gate, which this P0 phase must not disturb."""

    def test_two_equally_plausible_unlabeled_amounts_still_resolve_to_not_found(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("receipt", 50, 90),
            _word("2,500.00", 100, 150),
            _word("2,750.00", 100, 190),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value is None
        # Nothing to check magnitude against when total itself was never
        # resolved — None, not a guessed pass.
        assert result.arithmetic_validation.magnitude_plausible is None


class TestCaseE_LineItemConsistencyIsAStrongSignal:
    """No subtotal label at all — the line-item sum is the only available
    document-internal reference, and it agrees with total once tax is
    added on top."""

    def test_total_matching_the_line_item_sum_plus_tax_is_plausible(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("invoice", 50, 90),
            _word("Description", 50, 140), _word("Amount", 350, 140),
            _word("Widget", 50, 170), _word("1,000.00", 350, 170),
            _word("Gadget", 50, 200), _word("1,500.00", 350, 200),
            _word("Tax", 50, 260), _word("250.00", 350, 260),
            _word("Total", 50, 300), _word("2,750.00", 350, 300),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 2750.0
        assert result.arithmetic_validation.magnitude_plausible is True
        assert "total_magnitude_implausible" not in result.review_flags


class TestCaseF_UnrelatedMonetaryMentionsAreIgnored:
    """A much larger, unrelated number elsewhere on the page (a previous
    balance, not this invoice's own total) must never be confused for the
    total, and must never make the *correct* total look suspicious just
    because a bigger number exists somewhere on the same page."""

    def test_a_previous_balance_mention_never_becomes_the_total(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("invoice", 50, 90),
            _word("Previous", 50, 130), _word("Balance:", 130, 130), _word("277,000.00", 230, 130),
            _word("Subtotal", 50, 190), _word("2,500.00", 300, 190),
            _word("Tax", 50, 230), _word("250.00", 300, 230),
            _word("Total", 50, 270), _word("2,750.00", 300, 270),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 2750.0
        assert result.arithmetic_validation.magnitude_plausible is True
        assert "total_magnitude_implausible" not in result.review_flags


class TestCaseG_ApprovalMemoAmountsNeverBecomeATotal:
    """A monetary value inside approval/request prose must not become a
    transaction total merely because a number is present — document type/
    transactional state stays a separate concept from an arbitrary
    monetary mention (already-existing behavior, re-verified here in the
    same test file as the new plausibility cases so the whole "amount
    mentioned vs. total" boundary is visible in one place)."""

    def test_kindly_approve_wording_never_produces_a_total(self) -> None:
        words = [
            _word("Office", 50, 50), _word("Memo", 130, 50),
            _word("Please", 50, 100), _word("kindly", 110, 100), _word("approve", 180, 100),
            _word("Rs.", 260, 100), _word("50,000/-", 320, 100),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.document_type.value == "approval_request"
        assert result.transactional is False
        assert result.total.value is None
        assert result.amount_mentioned == 50000.0


class TestFieldLevelEvidenceIsAlwaysPresentOnTotal:
    """Every accepted total, not just a flagged one, carries the same
    evidence shape — a caller should never have to special-case "was this
    checked at all" vs. "was this found plausible"."""

    def test_a_document_with_no_reference_amount_at_all_still_gets_evidence(self) -> None:
        words = [
            _word("Northwind", 50, 50), _word("Traders", 130, 50),
            _word("receipt", 50, 90),
            _word("Total", 50, 200), _word("270.00", 300, 200),
        ]
        result = extract_invoice(_extraction_result(words))
        assert result.total.value == 270.0
        assert result.total.evidence is not None
        assert result.total.evidence["within_document_magnitude"] is None
        # Nothing to check against is never treated as a reason to review —
        # this is the exact same "missing evidence isn't a mismatch" stance
        # check_totals already takes.
        assert "total_magnitude_implausible" not in result.review_flags
