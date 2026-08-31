"""Section 6 — composite confidence scoring and threshold routing."""
from invoice_extraction.confidence import document_confidence, field_confidence, field_status, route


class TestFieldConfidence:
    def test_strong_method_and_clean_ocr_scores_highest(self) -> None:
        assert field_confidence(ocr_confidence=1.0, method="label_anchor+pattern") == 1.0

    def test_weak_fallback_method_scores_lower_even_with_clean_ocr(self) -> None:
        strong = field_confidence(1.0, "label_anchor+pattern")
        weak = field_confidence(1.0, "positional_fallback")
        assert weak < strong

    def test_noisy_ocr_pulls_the_score_down_even_with_a_strong_method(self) -> None:
        clean = field_confidence(1.0, "label_anchor+pattern")
        noisy = field_confidence(0.4, "label_anchor+pattern")
        assert noisy < clean

    def test_unknown_method_scores_zero_tier(self) -> None:
        assert field_confidence(1.0, "not_found") == 0.5  # (1.0 + 0.0) / 2


class TestDocumentConfidence:
    def test_critical_fields_weigh_more_than_minor_ones(self) -> None:
        strong_on_total = document_confidence(
            {"total": 1.0, "invoice_date": 0.2}, arithmetic_ok=True,
        )
        strong_on_date = document_confidence(
            {"total": 0.2, "invoice_date": 1.0}, arithmetic_ok=True,
        )
        assert strong_on_total > strong_on_date

    def test_a_failed_arithmetic_check_discounts_but_does_not_zero_the_score(self) -> None:
        passing = document_confidence({"total": 1.0}, arithmetic_ok=True)
        failing = document_confidence({"total": 1.0}, arithmetic_ok=False)
        assert 0 < failing < passing

    def test_no_fields_at_all_is_zero(self) -> None:
        assert document_confidence({}, arithmetic_ok=None) == 0.0


class TestRouting:
    def test_high_confidence_with_critical_fields_and_passing_arithmetic_auto_processes(self) -> None:
        assert route(score=0.9, critical_fields_present=True, arithmetic_ok=True) == "auto_processed"

    def test_missing_a_critical_field_forces_high_priority_review_regardless_of_score(self) -> None:
        assert route(score=0.95, critical_fields_present=False, arithmetic_ok=True) == "needs_review_high_priority"

    def test_a_failed_arithmetic_check_forces_at_least_needs_review(self) -> None:
        assert route(score=0.9, critical_fields_present=True, arithmetic_ok=False) == "needs_review"

    def test_a_very_low_score_is_high_priority_even_with_critical_fields_present(self) -> None:
        assert route(score=0.1, critical_fields_present=True, arithmetic_ok=None) == "needs_review_high_priority"

    def test_a_middling_score_is_plain_needs_review(self) -> None:
        assert route(score=0.6, critical_fields_present=True, arithmetic_ok=True) == "needs_review"


class TestFieldStatus:
    def test_an_absent_value_is_not_found_regardless_of_confidence(self) -> None:
        assert field_status(value_present=False, confidence=0.9) == "NOT_FOUND"

    def test_a_present_value_below_the_uncertain_threshold_is_uncertain(self) -> None:
        assert field_status(value_present=True, confidence=0.3) == "UNCERTAIN"

    def test_a_present_value_at_or_above_the_threshold_is_found(self) -> None:
        assert field_status(value_present=True, confidence=0.9) == "FOUND"
