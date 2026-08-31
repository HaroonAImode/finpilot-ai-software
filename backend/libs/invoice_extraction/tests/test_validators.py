"""Rule 3.3 — value-pattern validation as a filter, not a finder."""
from datetime import date

from invoice_extraction.validators import (
    find_date_in_text, is_day_month_ambiguous, is_plausible_invoice_number, is_plausible_ntn, parse_date, parse_money,
)


class TestParseDate:
    def test_slash_day_first(self) -> None:
        assert parse_date("24/10/2025") == date(2025, 10, 24)

    def test_dash_day_first(self) -> None:
        assert parse_date("06-05-2025") == date(2025, 5, 6)

    def test_iso_format(self) -> None:
        assert parse_date("2026-08-04") == date(2026, 8, 4)

    def test_month_name_day_year(self) -> None:
        assert parse_date("06 May 2025") == date(2025, 5, 6)

    def test_month_name_comma_year(self) -> None:
        assert parse_date("Jul 28, 2026") == date(2026, 7, 28)

    def test_garbage_returns_none_not_raise(self) -> None:
        assert parse_date("not a date") is None
        assert parse_date("") is None

    def test_two_digit_year_slash_format(self) -> None:
        """Real local-shop receipts commonly write the date this way
        ("20/8/26") — confirmed live against two real Pakistani receipts."""
        assert parse_date("20/8/26") == date(2026, 8, 20)

    def test_two_digit_year_dash_format(self) -> None:
        assert parse_date("20-8-26") == date(2026, 8, 20)

    def test_a_trailing_clock_time_is_stripped(self) -> None:
        """Regression guard for a real bug found live on a real e-commerce
        order-confirmation invoice: the label-anchored value for its date
        field was the full printed timestamp ("ORDER DATE Dec 14, 2020,
        4:18:34 PM"), which matched none of _DATE_FORMATS as one string —
        silently losing a date that was right there. None of the formats
        should (or need to) grow a time component; stripping the trailing
        time first is the fix."""
        assert parse_date("Dec 14, 2020, 4:18:34 PM") == date(2020, 12, 14)
        assert parse_date("Dec 14, 2020, 4:18") == date(2020, 12, 14)
        assert parse_date("24/10/2025, 09:05:00") == date(2025, 10, 24)

    def test_a_genuine_four_digit_year_is_not_misread_as_two_digit(self) -> None:
        """The 4-digit-year formats are tried first, so a real 4-digit year
        never gets truncated/misinterpreted by the 2-digit-year formats."""
        assert parse_date("20/8/2026") == date(2026, 8, 20)


class TestOrdinalSuffixNoise:
    """Regression guards for a real bug found live via the golden-dataset
    benchmark (docs/invoice-ocr-plan.md §17): an ordinal-day suffix
    ("1st"/"2nd"/"3rd"/"4th"..."31st") sometimes OCRs as something other
    than its own letters — a bare "t" (the "h" dropped) or a stray
    quotation mark standing in for a misread superscript. All examples
    below use generic, non-Pakistani-dataset dates and vendors."""

    def test_a_bare_t_ordinal_remnant_is_stripped(self) -> None:
        assert parse_date("3t April 2026") == date(2026, 4, 3)

    def test_a_quotation_mark_ordinal_remnant_is_stripped(self) -> None:
        assert parse_date('21" November 2026') == date(2026, 11, 21)

    def test_a_correctly_spelled_ordinal_suffix_still_works(self) -> None:
        """The normalization is additive — it must not break the case
        where OCR read the ordinal suffix perfectly fine to begin with."""
        assert parse_date("3rd April 2026") == date(2026, 4, 3)
        assert parse_date("1st January 2026") == date(2026, 1, 1)

    def test_ordinal_noise_glued_directly_onto_the_month_with_no_space(self) -> None:
        """Both the ordinal-suffix noise AND the missing separator can
        occur on the same date — a bare 't' immediately followed by the
        month name with no space at all."""
        assert parse_date("9tOctober 2026") == date(2026, 10, 9)


class TestMissingSeparatorBetweenDateComponents:
    """Regression guards for a second, distinct real bug found live in the
    same benchmark pass: a day/month or month/year boundary printing (or
    OCR'ing) with no separating space at all."""

    def test_day_glued_directly_onto_the_month_name(self) -> None:
        assert parse_date("14Aug 2026") == date(2026, 8, 14)

    def test_month_name_glued_directly_onto_the_year(self) -> None:
        assert parse_date("14 Aug2026") == date(2026, 8, 14)

    def test_both_boundaries_glued_at_once(self) -> None:
        assert parse_date("14Aug2026") == date(2026, 8, 14)

    def test_a_normally_spaced_date_is_unaffected(self) -> None:
        assert parse_date("14 Aug 2026") == date(2026, 8, 14)

    def test_a_word_immediately_followed_by_a_year_that_is_not_a_month_still_fails(self) -> None:
        """The normalization only widens which *substrings* look
        date-shaped enough to attempt — it must not turn an unrelated
        word+number combination into a false date."""
        assert parse_date("Invoice2026") is None


class TestFindDateInText:
    """Regression guards for a real bug found live scanning 35 real
    Pakistani petty-cash receipt photos through the deployed pipeline:
    invoice_date came back NOT_FOUND on every single one. Root cause was
    two real OCR shapes parse_date's strict whole-string match can never
    recover: a date glued directly onto its own label or a trailing time
    with zero separator character at all."""

    def test_still_handles_everything_parse_date_already_handles(self) -> None:
        assert find_date_in_text("24/10/2025") == date(2025, 10, 24)
        assert find_date_in_text("not a date at all") is None
        assert find_date_in_text("") is None

    def test_date_glued_directly_onto_a_trailing_time_with_no_separator(self) -> None:
        """Found live on a real receipt: '18/06/20264:30:13PM' — no comma,
        no space, nothing for _TRAILING_TIME_PATTERN to anchor a strip on.
        The fixed-width \\d{4} year group naturally stops at '2026',
        leaving the glued-on time untouched rather than swallowed into an
        unparseable 8-digit year."""
        assert find_date_in_text("18/06/20264:30:13PM") == date(2026, 6, 18)

    def test_date_glued_directly_onto_its_own_label_with_no_space(self) -> None:
        """Found live on a real receipt: 'Date:29/06/2026.16:29:41' arrives
        as a single OCR word with no space anywhere in it — the
        label-anchored path can never match 'date' as a standalone token
        inside this, so the whole field was silently lost until this."""
        assert find_date_in_text("Date:29/06/2026.16:29:41") == date(2026, 6, 29)

    def test_a_bare_date_with_no_label_at_all(self) -> None:
        """Found live: '16 Jun 2026' printed on a receipt with no 'Date:'
        label anywhere near it."""
        assert find_date_in_text("16 Jun 2026") == date(2026, 6, 16)

    def test_a_phone_number_is_not_misread_as_a_date(self) -> None:
        """Regression guard: a Pakistani phone number ('0345-5575555') has
        a hyphen too, but no substring of it has the day-hyphen-month-
        hyphen-4-digit-year shape a real date does."""
        assert find_date_in_text("0345-5575555,0301-5892562") is None

    def test_an_ordinal_noise_date_embedded_in_a_longer_line_is_found(self) -> None:
        """The substring-finding pattern, not just parse_date's own
        whole-string normalization, must tolerate the same ordinal-suffix
        noise — this is the path an unlabeled date embedded in a longer
        OCR'd line (a signature block, a memo footer) actually goes
        through."""
        assert find_date_in_text("Chief Executive Officer   3t April 2026") == date(2026, 4, 3)

    def test_a_glued_month_day_date_embedded_in_a_longer_line_is_found(self) -> None:
        assert find_date_in_text("14Aug 2026      10:43:05AM cashier:JOHN") == date(2026, 8, 14)


class TestIsDayMonthAmbiguous:
    """P1-C — general-pattern regression guards for the ambiguity signal
    that lets the candidate-selection layer discount (never guess away)
    a genuinely ambiguous numeric date. Every example is a generic,
    non-Pakistani-dataset date."""

    def test_both_components_twelve_or_under_is_ambiguous(self) -> None:
        assert is_day_month_ambiguous("06/02/2026") is True
        assert is_day_month_ambiguous("06-02-2026") is True

    def test_a_day_over_twelve_is_not_ambiguous(self) -> None:
        """Day 30 cannot be a month — the format is self-disambiguating."""
        assert is_day_month_ambiguous("30/06/2026") is False

    def test_a_two_digit_year_numeric_date_is_still_checked(self) -> None:
        assert is_day_month_ambiguous("06/02/26") is True

    def test_a_textual_month_name_date_is_never_ambiguous(self) -> None:
        assert is_day_month_ambiguous("10 June 2026") is False
        assert is_day_month_ambiguous("Jun 10, 2026") is False

    def test_an_iso_date_is_never_ambiguous(self) -> None:
        """The leading 4-digit year makes ISO's own field order
        unambiguous regardless of the day/month values."""
        assert is_day_month_ambiguous("2026-02-06") is False

    def test_text_with_no_date_at_all_is_not_ambiguous(self) -> None:
        assert is_day_month_ambiguous("Order #12/34") is False
        assert is_day_month_ambiguous("") is False

    def test_a_date_glued_directly_onto_a_trailing_time_is_still_checked(self) -> None:
        """Found live: 'Booked At 06/02/202613.05:15' — a trailing time
        glued onto the year with zero separator. The fixed \\d{4} year
        width (not a trailing boundary) is what lets this still be
        recognized as day='06'/month='02', both <= 12."""
        assert is_day_month_ambiguous("Booked At 06/02/202613.05:15") is True

    def test_an_unambiguous_day_glued_onto_a_trailing_time_is_not_flagged(self) -> None:
        assert is_day_month_ambiguous("18/06/20264:30:13PM") is False


class TestParseMoney:
    def test_plain_decimal(self) -> None:
        assert parse_money("183254") == 183254.0
        assert parse_money("1234.56") == 1234.56

    def test_thousands_separator(self) -> None:
        assert parse_money("1,234.56") == 1234.56
        assert parse_money("Rs 6,000") == 6000.0

    def test_currency_prefix_and_suffix(self) -> None:
        assert parse_money("PKR 1,234.00") == 1234.0
        assert parse_money("$1.00 USD") == 1.0

    def test_no_number_present_returns_none(self) -> None:
        assert parse_money("N/A") is None
        assert parse_money("") is None


class TestInvoiceNumberPlausibility:
    def test_real_shapes_are_accepted(self) -> None:
        for candidate in ["INV-2026-1841", "M180362221", "BK-20260205-002", "565151287"]:
            assert is_plausible_invoice_number(candidate), candidate

    def test_a_bare_one_or_two_digit_number_is_rejected(self) -> None:
        """A page number or item count is far more likely than a real
        invoice number this short."""
        assert not is_plausible_invoice_number("5")
        assert not is_plausible_invoice_number("12")

    def test_empty_is_rejected(self) -> None:
        assert not is_plausible_invoice_number("")


class TestNtnPlausibility:
    def test_seven_digit_ntn_is_accepted(self) -> None:
        assert is_plausible_ntn("3947261")

    def test_ntn_with_check_digit_is_accepted(self) -> None:
        assert is_plausible_ntn("3947261-8")

    def test_non_numeric_is_rejected(self) -> None:
        assert not is_plausible_ntn("ABC1234")


class TestNegativeAmounts:
    def test_a_leading_minus_is_preserved(self) -> None:
        """Real Pakistani payment-summary table writes an advance already
        paid as "-1,000". Dropping the sign turns a deduction into a charge
        — an error that reconciles to exactly twice the amount."""
        assert parse_money("-1,000") == -1000.0
        assert parse_money("PKR -500.25") == -500.25

    def test_accounting_style_parentheses_are_negative(self) -> None:
        assert parse_money("(1,000)") == -1000.0

    def test_a_bare_dash_is_not_a_number(self) -> None:
        """An empty cell is often printed as "-"; it must stay not-found
        rather than becoming 0."""
        assert parse_money("-") is None

    def test_positive_amounts_are_unaffected(self) -> None:
        assert parse_money("4,000") == 4000.0
        assert parse_money("Rs. 1,234.56") == 1234.56
