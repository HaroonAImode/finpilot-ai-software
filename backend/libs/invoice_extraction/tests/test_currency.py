"""Currency detection — must never default, and must weigh a bare symbol
as weaker evidence than an explicit word/code (see currency.py's own
docstring for the real garbled-OCR false positive that motivated this)."""
from invoice_extraction.currency import detect_currency


class TestWordAndCodeMatches:
    def test_pkr_code_is_detected(self) -> None:
        result = detect_currency("Total: PKR 1,500")
        assert result.value == "PKR"
        assert result.confidence == 1.0

    def test_rs_with_trailing_period_is_detected(self) -> None:
        result = detect_currency("Amount (Rs.) 350")
        assert result.value == "PKR"

    def test_rs_without_period_is_detected(self) -> None:
        result = detect_currency("Total Rs 350")
        assert result.value == "PKR"

    def test_rupees_word_is_detected(self) -> None:
        result = detect_currency("Five hundred Rupees only")
        assert result.value == "PKR"

    def test_usd_code_is_detected(self) -> None:
        result = detect_currency("Total USD 100.00")
        assert result.value == "USD"

    def test_eur_code_is_detected(self) -> None:
        result = detect_currency("Total EUR 100.00")
        assert result.value == "EUR"

    def test_gbp_code_is_detected(self) -> None:
        result = detect_currency("Total GBP 100.00")
        assert result.value == "GBP"


class TestSymbolMatches:
    def test_dollar_sign_next_to_an_amount_is_detected_but_weaker(self) -> None:
        result = detect_currency("Total $36.36")
        assert result.value == "USD"
        assert result.confidence < 1.0
        assert result.method == "pattern_match_weak"

    def test_a_lone_dollar_sign_with_no_adjacent_digit_is_not_evidence(self) -> None:
        """Regression guard for the real bug found live: garbled OCR from a
        company logo produced a bare '$' with no digit anywhere near it and
        no real currency mention on the document at all."""
        result = detect_currency("$$ : ; some unrelated garbled text")
        assert result.value is None

    def test_a_symbol_next_to_a_single_stray_digit_is_reported_as_uncertain(self) -> None:
        """Regression guard for the exact real false positive found live:
        'SAMSUDDIN INVOICE SIDDIQUI $9 Samsuddin' — a misrecognized logo
        mark next to a stray digit, on a document that is actually priced
        in Rs, not USD. Can't be filtered out by pattern alone (it does
        look like currency-shaped text), so it must at least be reported at
        low enough confidence to route to review rather than presented as
        fact."""
        result = detect_currency("SAMSUDDIN INVOICE SIDDIQUI $9 Samsuddin 90224 18585")
        assert result.value == "USD"
        assert result.confidence < 0.5  # below confidence.field_status's UNCERTAIN threshold


class TestNoEvidence:
    def test_no_currency_mention_returns_not_found_never_a_default(self) -> None:
        """The exact bug this module exists to avoid: a separate reference
        project's currency detector unconditionally defaulted to USD when
        nothing matched. This one must never do that."""
        result = detect_currency("Vendor: ABC Traders\nItem: Widget\nQty: 1")
        assert result.value is None
        assert result.method == "not_found"

    def test_empty_text_returns_not_found(self) -> None:
        result = detect_currency("")
        assert result.value is None
