"""match.suggest_matches — rules-based string similarity, no LLM. See the
module's own docstring for why a suggestion is ranked here but never
applied automatically."""
import uuid

from app.models import Vendor, VendorStatus
from app.services.match import MAX_SUGGESTIONS, MIN_SCORE, suggest_matches


def _vendor(name: str) -> Vendor:
    return Vendor(id=uuid.uuid4(), company_id=uuid.uuid4(), name=name, status=VendorStatus.active)


class TestSuggestMatches:
    def test_a_near_identical_ocr_misread_scores_highest(self) -> None:
        vendors = [_vendor("ABC Traders"), _vendor("Karachi Steel Co")]
        suggestions = suggest_matches("ABC Tradres", vendors)
        assert suggestions[0].name == "ABC Traders"

    def test_case_and_spacing_do_not_affect_the_score(self) -> None:
        vendors = [_vendor("ABC Traders")]
        assert suggest_matches("  abc   traders  ", vendors)[0].score == 1.0

    def test_an_unrelated_name_produces_no_suggestions(self) -> None:
        vendors = [_vendor("ABC Traders")]
        assert suggest_matches("Completely Different Supplier Co", vendors) == []

    def test_no_vendors_means_no_suggestions(self) -> None:
        assert suggest_matches("Anything", []) == []

    def test_at_most_three_suggestions_are_returned(self) -> None:
        vendors = [_vendor(f"ABC Trading Co {i}") for i in range(10)]
        assert len(suggest_matches("ABC Trading Co", vendors)) <= MAX_SUGGESTIONS

    def test_scores_below_the_threshold_are_dropped(self) -> None:
        vendors = [_vendor("Z")]
        suggestions = suggest_matches("Completely unrelated name here", vendors)
        assert all(s.score >= MIN_SCORE for s in suggestions)
