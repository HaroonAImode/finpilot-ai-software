"""Synthetic unit tests for the field-comparison logic — no real documents,
no OCR, no pipeline involved. Covers every status the benchmark spec
requires: CORRECT, INCORRECT, MISSING, UNVERIFIABLE, FALSE_POSITIVE, plus
the numeric- and date-aware comparisons Rule 5 requires.
"""
import pytest

from golden_eval.compare import (
    ABSENT, UNKNOWN, FieldStatus, compare_amount, compare_date, compare_exact, compare_text,
)


class TestCorrectField:
    def test_exact_text_match_is_correct(self):
        assert compare_text("vendor", "Riverside Traders", "Riverside Traders").status == FieldStatus.CORRECT

    def test_case_and_whitespace_differences_still_count_as_correct(self):
        assert compare_text("vendor", "Riverside Traders", "  riverside traders  ").status == FieldStatus.CORRECT

    def test_matching_amount_is_correct(self):
        assert compare_amount("total", 2200.0, 2200.0).status == FieldStatus.CORRECT

    def test_float_rounding_noise_still_counts_as_correct(self):
        """2200.00 round-tripped through JSON/float arithmetic must not
        register as a mismatch against the same value."""
        assert compare_amount("total", 2200.0, 2199.999).status == FieldStatus.CORRECT

    def test_differently_formatted_equal_dates_are_correct(self):
        assert compare_date("invoice_date", "2026-06-18", "18/06/2026").status == FieldStatus.CORRECT


class TestIncorrectField:
    def test_a_wrong_text_value_is_incorrect_not_missing(self):
        assert compare_text("vendor", "Azeem Electric & Hardware Store", "Azeem").status == FieldStatus.INCORRECT

    def test_a_materially_wrong_amount_is_incorrect(self):
        """The exact example from the spec: expected 500, actual 50 — a
        critical error, never treated as equivalent to no data at all."""
        result = compare_amount("total", 500, 50)
        assert result.status == FieldStatus.INCORRECT

    def test_a_wrong_date_is_incorrect(self):
        assert compare_date("invoice_date", "2026-06-18", "2026-06-19").status == FieldStatus.INCORRECT

    def test_a_small_but_real_amount_difference_is_incorrect_not_correct(self):
        """The tolerance exists for float rounding noise only — a
        genuinely different amount must never slip through it."""
        assert compare_amount("total", 5960.0, 5961.79).status == FieldStatus.INCORRECT


class TestMissingField:
    def test_no_actual_value_is_missing_when_ground_truth_is_known(self):
        result = compare_amount("total", 500, None)
        assert result.status == FieldStatus.MISSING

    def test_missing_is_distinguishable_from_incorrect_for_the_same_expectation(self):
        """The spec's own example: Expected 500 / Actual UNKNOWN -> MISSING,
        Expected 500 / Actual 50 -> INCORRECT. Same expected value, two
        different, never-conflated outcomes."""
        missing = compare_amount("total", 500, None)
        wrong = compare_amount("total", 500, 50)
        assert missing.status != wrong.status
        assert missing.status == FieldStatus.MISSING
        assert wrong.status == FieldStatus.INCORRECT

    def test_blank_string_actual_counts_as_missing_for_text_fields(self):
        assert compare_text("vendor", "Riverside Traders", "   ").status == FieldStatus.MISSING


class TestUnverifiableGroundTruth:
    def test_unknown_expected_value_is_unverifiable_regardless_of_actual(self):
        assert compare_text("vendor", UNKNOWN, "Anything At All").status == FieldStatus.UNVERIFIABLE

    def test_unverifiable_even_when_actual_is_also_missing(self):
        assert compare_amount("total", UNKNOWN, None).status == FieldStatus.UNVERIFIABLE


class TestFalsePositiveDetection:
    def test_a_value_where_none_was_expected_is_a_false_positive(self):
        """A confirmed non-transactional document's `total` should always
        be absent — the actual pipeline reporting a number anyway is the
        single most dangerous shape of error this benchmark exists to
        catch, and must never be conflated with an ordinary INCORRECT."""
        result = compare_amount("total", ABSENT, 22875.0)
        assert result.status == FieldStatus.FALSE_POSITIVE

    def test_absent_expectation_correctly_met_is_correct(self):
        assert compare_amount("total", ABSENT, None).status == FieldStatus.CORRECT

    def test_false_positive_applies_to_text_fields_too(self):
        result = compare_text("category", ABSENT, "Office / Misc Supplies")
        assert result.status == FieldStatus.FALSE_POSITIVE


class TestNumericAmountComparisonIsNeverStringBased:
    @pytest.mark.parametrize("expected,actual", [(2200, "2200.0"), (2200, 2200.0), ("2200", 2200.0)])
    def test_numeric_and_string_shaped_equal_amounts_still_match(self, expected, actual):
        assert compare_amount("total", expected, actual).status == FieldStatus.CORRECT

    def test_a_non_numeric_actual_value_is_incorrect_not_a_crash(self):
        result = compare_amount("total", 500, "not-a-number")
        assert result.status == FieldStatus.INCORRECT


class TestDateComparisonIsNeverStringBased:
    def test_a_golden_dataset_authoring_bug_raises_rather_than_silently_scoring(self):
        """An unparseable *expected* value is a benchmark-authoring error,
        not a pipeline result — it must be surfaced loudly, not scored as
        a pass or fail."""
        with pytest.raises(ValueError):
            compare_date("invoice_date", "not-a-date", "2026-06-18")

    def test_an_unparseable_actual_date_is_incorrect(self):
        assert compare_date("invoice_date", "2026-06-18", "garbled-ocr-noise").status == FieldStatus.INCORRECT


class TestExactComparisonForClosedVocabularyFields:
    def test_document_type_mismatch_is_incorrect(self):
        assert compare_exact("document_type", "invoice", "receipt").status == FieldStatus.INCORRECT

    def test_boolean_transactional_field_matches(self):
        assert compare_exact("transactional", True, True).status == FieldStatus.CORRECT
        assert compare_exact("transactional", True, False).status == FieldStatus.INCORRECT

    def test_boolean_false_is_not_treated_as_missing(self):
        """`False` must never trip the "blank means missing" check the way
        `None` or an empty string does — it's a real, present answer."""
        assert compare_exact("transactional", False, False).status == FieldStatus.CORRECT
