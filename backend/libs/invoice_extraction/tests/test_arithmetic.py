"""Section 5 — arithmetic/cross-field validation, the cheapest and
strongest confidence signal available (Rule 5 preamble)."""
from datetime import date

from invoice_extraction.arithmetic import (
    check_line_item, check_totals, is_plausible_invoice_date, is_plausible_tax_rate,
    is_plausible_total_magnitude, shares_source_location, total_reference_amount,
)


class TestLineItemCheck:
    def test_exact_match_passes(self) -> None:
        assert check_line_item(qty=12, rate=9800, amount=117600) == "pass"

    def test_within_rounding_tolerance_passes(self) -> None:
        assert check_line_item(qty=3, rate=33.33, amount=99.99) == "pass"

    def test_mismatch_fails(self) -> None:
        """A misread digit shifting a value materially — the exact failure
        mode Rule 4.4 calls out (column drift landing a value under the
        wrong header)."""
        assert check_line_item(qty=12, rate=9800, amount=98000) == "fail"

    def test_missing_any_value_is_not_checked_not_failed(self) -> None:
        assert check_line_item(qty=None, rate=9800, amount=117600) == "not_checked"
        assert check_line_item(qty=12, rate=None, amount=117600) == "not_checked"
        assert check_line_item(qty=12, rate=9800, amount=None) == "not_checked"


class TestTotalsCheck:
    def test_subtotal_plus_tax_equals_total(self) -> None:
        assert check_totals(subtotal=155300, tax=27954, discount=None, total=183254) is True

    def test_with_a_discount(self) -> None:
        assert check_totals(subtotal=1000, tax=180, discount=100, total=1080) is True

    def test_mismatch_is_false(self) -> None:
        assert check_totals(subtotal=1000, tax=180, discount=None, total=5000) is False

    def test_missing_subtotal_or_total_is_none_not_false(self) -> None:
        """None means 'nothing to check', a materially different, weaker
        signal than a genuine mismatch — callers must not treat the two the
        same way."""
        assert check_totals(subtotal=None, tax=180, discount=None, total=1180) is None
        assert check_totals(subtotal=1000, tax=180, discount=None, total=None) is None


class TestTaxRatePlausibility:
    def test_a_standard_pakistan_rate_is_plausible(self) -> None:
        assert is_plausible_tax_rate(subtotal=1000, tax_amount=180) is True  # 18%

    def test_an_arbitrary_rate_is_implausible(self) -> None:
        assert is_plausible_tax_rate(subtotal=1000, tax_amount=37) is False  # 3.7%

    def test_missing_inputs_is_none(self) -> None:
        assert is_plausible_tax_rate(subtotal=None, tax_amount=180) is None
        assert is_plausible_tax_rate(subtotal=1000, tax_amount=None) is None


class TestDatePlausibility:
    def test_a_recent_past_date_is_plausible(self) -> None:
        assert is_plausible_invoice_date(date(2026, 8, 1), today=date(2026, 8, 21)) is True

    def test_a_future_date_is_implausible(self) -> None:
        """Catches an OCR digit confusion (0/8, 1/7) that shifted the year
        forward, per Rule 5.4."""
        assert is_plausible_invoice_date(date(2027, 1, 1), today=date(2026, 8, 21)) is False

    def test_a_multi_year_old_date_is_implausible(self) -> None:
        assert is_plausible_invoice_date(date(2020, 1, 1), today=date(2026, 8, 21)) is False

    def test_missing_date_is_none(self) -> None:
        assert is_plausible_invoice_date(None, today=date(2026, 8, 21)) is None


class TestTotalReferenceAmount:
    """P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md
    P0-2/P0-3)."""

    def test_prefers_subtotal_plus_tax_minus_discount_when_a_subtotal_exists(self) -> None:
        assert total_reference_amount(subtotal=2500, tax=250, discount=None, line_items_amount_sum=999999) == 2750

    def test_falls_back_to_line_item_sum_when_no_subtotal_was_extracted(self) -> None:
        assert total_reference_amount(subtotal=None, tax=None, discount=None, line_items_amount_sum=2750) == 2750

    def test_neither_available_is_none(self) -> None:
        assert total_reference_amount(subtotal=None, tax=None, discount=None, line_items_amount_sum=None) is None


class TestTotalMagnitudePlausibility:
    def test_a_matching_total_is_plausible(self) -> None:
        assert is_plausible_total_magnitude(total=2750, reference=2750) is True

    def test_a_legitimately_large_but_consistent_total_is_plausible(self) -> None:
        """A fixed ceiling would reject this outright — the whole point of
        a ratio-based check is that a large invoice is exactly as plausible
        as a small one when it agrees with its own subtotal+tax."""
        assert is_plausible_total_magnitude(total=990000, reference=990000) is True

    def test_a_total_two_orders_of_magnitude_off_is_implausible(self) -> None:
        """The exact real shape found live: subtotal+tax ~= 2,750, total
        resolved to 277,000 — a ~100x ratio."""
        assert is_plausible_total_magnitude(total=277000, reference=2750) is False

    def test_a_total_far_too_small_is_also_implausible(self) -> None:
        assert is_plausible_total_magnitude(total=27.5, reference=2750) is False

    def test_a_reasonable_discount_or_rounding_difference_is_still_plausible(self) -> None:
        assert is_plausible_total_magnitude(total=2200, reference=2750) is True

    def test_no_reference_amount_is_none_not_a_guessed_pass(self) -> None:
        assert is_plausible_total_magnitude(total=2750, reference=None) is None

    def test_no_total_is_none(self) -> None:
        assert is_plausible_total_magnitude(total=None, reference=2750) is None


class TestSharesSourceLocation:
    def test_identical_page_and_bbox_is_a_collision(self) -> None:
        assert shares_source_location(0, (10.0, 20.0, 30.0, 40.0), 0, (10.0, 20.0, 30.0, 40.0)) is True

    def test_different_bbox_on_the_same_page_is_not_a_collision(self) -> None:
        assert shares_source_location(0, (10.0, 20.0, 30.0, 40.0), 0, (10.0, 90.0, 30.0, 110.0)) is False

    def test_same_bbox_on_a_different_page_is_not_a_collision(self) -> None:
        assert shares_source_location(0, (10.0, 20.0, 30.0, 40.0), 1, (10.0, 20.0, 30.0, 40.0)) is False

    def test_either_side_missing_location_data_is_not_a_collision(self) -> None:
        assert shares_source_location(None, None, 0, (10.0, 20.0, 30.0, 40.0)) is False
        assert shares_source_location(0, (10.0, 20.0, 30.0, 40.0), None, None) is False
