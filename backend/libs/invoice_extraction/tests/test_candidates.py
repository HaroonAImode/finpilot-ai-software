"""P1 — the shared candidate/selection primitives, tested entirely in
isolation from any real field (vendor, total, date, ...). This is
deliberately the FIRST thing tested in the P1 phase, before any field is
touched, so a regression here can never be confused with a vendor-specific
regression — see fields.py's own tests for the vendor application of this
architecture.
"""
import pytest

from invoice_extraction.candidates import Candidate, select_candidate


def _candidate(value: str, **evidence: float) -> Candidate:
    return Candidate(field_name="test_field", value=value, method="synthetic", evidence=evidence)


class TestCandidateScoreIsDerivedFromEvidence:
    def test_score_sums_every_named_contribution(self) -> None:
        c = _candidate("A", label_match=1.0, position=0.3, ocr_confidence=0.2)
        assert c.score == 1.5

    def test_negative_evidence_reduces_the_score_like_any_other_signal(self) -> None:
        """No separate 'penalty' mechanism — a negative contribution is
        just a negative number living in the same dict."""
        c = _candidate("A", position=0.3, looks_like_reference_number=-1.0)
        assert c.score == -0.7

    def test_no_evidence_at_all_is_a_zero_score_not_an_error(self) -> None:
        assert _candidate("A").score == 0.0

    def test_score_is_always_recomputed_never_stale(self) -> None:
        """A frozen dataclass with no separately-stored score — there is
        nothing that could drift out of sync with the evidence behind it."""
        c = _candidate("A", x=1.0)
        assert c.score == 1.0
        assert c.score == sum(c.evidence.values())


class TestSelectionWithNoCandidates:
    def test_returns_unknown_not_an_error(self) -> None:
        result = select_candidate([], ambiguity_margin=0.1)
        assert result.status == "unknown"
        assert result.winner is None
        assert result.margin is None
        assert result.candidates == []


class TestSelectionWithOneCandidate:
    def test_a_lone_candidate_is_always_accepted_regardless_of_its_own_score(self) -> None:
        """Whether a single weak candidate should be trusted is a
        confidence-tier question for the caller (baked into that
        candidate's own evidence) — this function has no opinion of its
        own about a lone candidate's absolute score, only about how it
        compares to a rival that doesn't exist here."""
        weak = _candidate("A", ocr_confidence=0.1)
        result = select_candidate([weak], ambiguity_margin=0.5)
        assert result.status == "accept"
        assert result.winner is weak
        assert result.margin is None


class TestSelectionWithAClearWinner:
    def test_the_higher_scoring_candidate_wins_and_is_accepted(self) -> None:
        strong = _candidate("Strong", ocr_confidence=10.0)
        weak = _candidate("Weak", ocr_confidence=1.0)
        result = select_candidate([weak, strong], ambiguity_margin=0.5)
        assert result.status == "accept"
        assert result.winner is strong
        assert result.margin == 9.0

    def test_candidates_are_returned_sorted_strongest_first_for_explainability(self) -> None:
        """A review UI (or a future audit) needs to see every candidate
        that was considered, not just the bare winner, to answer 'why did
        this one win over that one?'."""
        a = _candidate("A", ocr_confidence=1.0)
        b = _candidate("B", ocr_confidence=5.0)
        c = _candidate("C", ocr_confidence=3.0)
        result = select_candidate([a, b, c], ambiguity_margin=0.1)
        assert [cand.value for cand in result.candidates] == ["B", "C", "A"]

    def test_negative_evidence_can_flip_which_candidate_wins(self) -> None:
        """The whole point of a signed-evidence model: a candidate with a
        large positive signal but a disqualifying negative one must still
        lose to a modest, untainted candidate."""
        tainted = _candidate("Tainted", ocr_confidence=10.0, looks_like_reference_number=-1000.0)
        modest = _candidate("Modest", ocr_confidence=2.0)
        result = select_candidate([tainted, modest], ambiguity_margin=0.5)
        assert result.winner is modest


class TestSelectionHandlesAmbiguity:
    """The P1 task's own explicit requirement: 'do not assume highest
    score = automatically correct.' A margin too thin to trust must be
    surfaced as REVIEW, never silently resolved as if it were a confident
    ACCEPT."""

    def test_two_nearly_tied_candidates_are_review_not_an_arbitrary_pick(self) -> None:
        a = _candidate("A", ocr_confidence=5.0)
        b = _candidate("B", ocr_confidence=5.01)
        result = select_candidate([a, b], ambiguity_margin=0.5)
        assert result.status == "review"
        # A winner is still named — ambiguity is a caller's confidence
        # concern to act on (e.g. degrade confidence, keep the value), not
        # this function's decision to withhold a value outright.
        assert result.winner is b
        assert result.margin == pytest.approx(0.01)

    def test_a_margin_exactly_at_the_threshold_is_accepted_not_reviewed(self) -> None:
        a = _candidate("A", ocr_confidence=1.0)
        b = _candidate("B", ocr_confidence=1.5)
        result = select_candidate([a, b], ambiguity_margin=0.5)
        assert result.status == "accept"

    def test_a_margin_just_under_the_threshold_is_reviewed(self) -> None:
        a = _candidate("A", ocr_confidence=1.0)
        b = _candidate("B", ocr_confidence=1.49)
        result = select_candidate([a, b], ambiguity_margin=0.5)
        assert result.status == "review"


class TestProvenanceSurvivesSelection:
    def test_the_winning_candidates_own_method_page_and_bbox_are_preserved(self) -> None:
        c = Candidate(
            field_name="vendor_name", value="Northwind Traders", method="positional_fallback",
            page=0, bbox=(10.0, 20.0, 100.0, 40.0), evidence={"ocr_confidence": 5.0},
        )
        result = select_candidate([c], ambiguity_margin=0.5)
        assert result.winner.method == "positional_fallback"
        assert result.winner.page == 0
        assert result.winner.bbox == (10.0, 20.0, 100.0, 40.0)

    def test_context_is_never_consulted_by_scoring_or_selection(self) -> None:
        """context is for debugging/explainability only — stuffing
        anything into it, including something that looks score-shaped,
        must never influence which candidate wins."""
        trap = Candidate(
            field_name="test_field", value="A", method="synthetic",
            evidence={"ocr_confidence": 1.0}, context={"score": 999.0},
        )
        real = Candidate(field_name="test_field", value="B", method="synthetic", evidence={"ocr_confidence": 2.0})
        result = select_candidate([trap, real], ambiguity_margin=0.1)
        assert result.winner is real
