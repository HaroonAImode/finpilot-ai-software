"""classify_category — P1-E's own scored Candidate/select_candidate
architecture (see app/services/category_classifier.py's own module
docstring for the full rule-order bug this replaces). "Starting point, not
a verdict" reasoning is unchanged from before this phase.
"""
import pytest

from app.models import SUGGESTED_CATEGORIES
from app.services.category_classifier import _CATEGORY_KEYWORDS, classify_category


def test_every_keyword_category_is_a_real_suggested_category() -> None:
    """Catches a typo/rename in either table before it ships — an auto-
    assigned category that isn't in the suggestion list would show up as a
    perfectly normal-looking, silently-orphaned category on Saved Records."""
    for entry in _CATEGORY_KEYWORDS:
        assert entry.category in SUGGESTED_CATEGORIES, f"{entry.category!r} is not in SUGGESTED_CATEGORIES"


@pytest.mark.parametrize(
    "vendor_name,expected",
    [
        ("KFC Gulberg", "Office Entertainment"),
        ("Dominos Pizza", "Office Entertainment"),
        # Found live: "Layers Bakeshop", a real, correctly-extracted vendor
        # in the 35-receipt dataset with zero prior keyword coverage.
        ("Layers Bakeshop", "Office Entertainment"),
        ("Coursera Inc.", "Employee Training & Education"),
        ("Al-Falah Law Associates", None),  # "law" alone is not a keyword — see below for "legal fee"
        ("Shell Petrol Pump", "Vehicle Running & Maintenance"),
        ("City Electrician Services — repair", "Office Repair & Maintenance"),
        ("Karachi Water Supplier — mineral water", "Office / Misc Supplies"),
        ("ABC Stationery Mart", "Stationary / Others"),
        ("K-Electric", "Utilities"),
        ("Uber Pakistan", "Fuel & Transport"),
        # Found live during the uncategorized-transactional-documents audit
        # (docs/invoice-ocr-plan.md §13): a real vendor, correctly
        # extracted at the time — see TestStalePapajohnsWorkaroundRemoved
        # below for why the literal "papajohns" keyword itself was removed
        # in this phase, and why "papa john's" (the real, correctly-
        # spelled brand name) is kept as general, not stale, evidence.
        ("Papa John's", "Office Entertainment"),
        ("FSO DHA Filling Station", "Vehicle Running & Maintenance"),
    ],
)
def test_vendor_name_keyword_matches(vendor_name, expected) -> None:
    assert classify_category(vendor_name=vendor_name, item_descriptions=[], filename=None) == expected


def test_item_descriptions_are_also_searched() -> None:
    result = classify_category(
        vendor_name="Generic Traders", item_descriptions=["Coursera course fee — AI Engineering"], filename=None,
    )
    assert result == "Employee Training & Education"


def test_filename_is_also_searched() -> None:
    result = classify_category(vendor_name=None, item_descriptions=[], filename="salary-slip-june.pdf")
    assert result == "Salary"


def test_no_keyword_hit_returns_none_rather_than_a_guess() -> None:
    assert classify_category(vendor_name="Unrelated Traders Pvt Ltd", item_descriptions=[], filename=None) is None


def test_no_input_at_all_returns_none() -> None:
    assert classify_category(vendor_name=None, item_descriptions=[None, None], filename=None) is None


def test_matching_is_case_insensitive() -> None:
    assert classify_category(vendor_name="KENTUCKY FRIED CHICKEN kfc", item_descriptions=[], filename=None) == "Office Entertainment"


class TestStalePapajohnsWorkaroundRemoved:
    """P1-E audit (docs/invoice-ocr-plan.md §24): the literal "papajohns"
    keyword existed only because vendor extraction used to (incorrectly)
    return a vendor's own transaction email address wholesale
    ("papajohns@livepepper.com") — a workaround for a bug in a *different*
    field, fixed in P1-B (vendor now correctly rejects email addresses as
    candidates entirely). Confirmed dead code before removal: no vendor
    text anywhere in the real 35-document Golden Dataset still produces
    the literal glued substring "papajohns" post-P1-B. Removing it is safe
    and does not weaken real coverage — "papa john's" (the brand's own
    correctly spelled name) remains a real, general keyword."""

    def test_the_stale_glued_email_fragment_no_longer_matches_anything(self) -> None:
        assert classify_category(vendor_name="papajohns@livepepper.com", item_descriptions=[], filename=None) is None

    def test_the_real_brand_name_still_matches(self) -> None:
        assert classify_category(vendor_name="Papa John's", item_descriptions=[], filename=None) == "Office Entertainment"


class TestGenericStoreFallback:
    """Regression guard for a real gap found live scanning 35 real
    petty-cash receipts: "Express Mart" and "Falcon Cash & Carry" — two of
    the most common vendors in that dataset, both correctly extracted —
    matched nothing in this table at all and fell to Uncategorized."""

    @pytest.mark.parametrize(
        "vendor_name",
        ["Express Mart", "Rs1100 Express Mart", "Falcon Cash & Carry", "City General Store", "ABC Super Store"],
    )
    def test_generic_store_vendor_names_land_in_misc_supplies(self, vendor_name) -> None:
        assert classify_category(vendor_name=vendor_name, item_descriptions=[], filename=None) == "Office / Misc Supplies"

    def test_a_more_specific_category_still_wins_over_the_generic_store_fallback(self) -> None:
        """The generic "mart" fallback is deliberately the weakest tier —
        a vendor name that also matches a more specific keyword (here,
        "stationery") must resolve to that category, not the generic one."""
        assert classify_category(vendor_name="ABC Stationery Mart", item_descriptions=[], filename=None) == "Stationary / Others"

    def test_smart_is_not_misread_as_a_generic_store(self) -> None:
        """"mart" is now matched word-boundary-aware (P1-E generalizes the
        old "leading space" trick to every keyword) — "Smart Watch Store"
        must not match via a bare substring of "mart" glued onto "S"."""
        assert classify_category(vendor_name="Smart Watch Store", item_descriptions=[], filename=None) is None


class TestWordBoundaryMatchingClosesRealFalsePositiveRisks:
    """P1-E audit (docs/invoice-ocr-plan.md §24): the previous plain-
    substring matcher let "rent" match inside "different"/"current"/
    "parent"/"torrent", and "toilet" match inside "toiletries" — real,
    general false-positive risks, confirmed by direct construction (the
    same standard P1-B/C/D held themselves to for the vendor/date/total
    collisions each of those phases found), not yet observed in the
    Golden Dataset but not something to wait for a real failure to fix
    either."""

    def test_rent_does_not_match_inside_common_unrelated_words(self) -> None:
        for word in ("different", "current", "parent", "apparent", "torrent"):
            assert classify_category(vendor_name=None, item_descriptions=[f"a {word} item"], filename=None) is None

    def test_rent_still_matches_as_its_own_word(self) -> None:
        assert classify_category(vendor_name="City Properties — rent", item_descriptions=[], filename=None) == "Rent"

    def test_toilet_does_not_match_inside_toiletries(self) -> None:
        assert classify_category(vendor_name=None, item_descriptions=["toiletries"], filename=None) is None

    def test_toilet_still_matches_as_its_own_word(self) -> None:
        assert classify_category(vendor_name="ABC Plumbers", item_descriptions=["toilet repair"], filename=None) == "Office Repair & Maintenance"


class TestHardwareStoreVendorType:
    """P1-E Golden Dataset error analysis: "Azeem Electric & Hardware
    Store" — a correctly-extracted real vendor, zero prior keyword
    coverage despite its own line items (a toilet fitting, a power plug)
    squarely being repair/maintenance supplies. General vendor-*type*
    wording, not this one shop's own name."""

    def test_a_hardware_store_vendor_lands_in_repair_and_maintenance(self) -> None:
        assert classify_category(
            vendor_name="Azeem Electric & Hardware Store", item_descriptions=[], filename=None,
        ) == "Office Repair & Maintenance"

    def test_bare_hardware_store_wording_also_matches(self) -> None:
        assert classify_category(vendor_name="City Hardware Store", item_descriptions=[], filename=None) == "Office Repair & Maintenance"


class TestSoftDrinkBrandsAreOfficeEntertainment:
    """P1-E Golden Dataset error analysis: a real "Cash & Carry" grocery-
    type vendor (weak, Office / Misc Supplies) whose own line items were
    specific soft-drink brand names was previously misclassified as the
    generic-store fallback — the drinks were bought as office refreshments,
    not general supplies. General market brand names, not this one vendor."""

    def test_named_soft_drinks_outrank_the_generic_store_fallback(self) -> None:
        result = classify_category(
            vendor_name="Falcon Cash & Carry", item_descriptions=["Sprite", "Mirinda", "Pepsi"], filename=None,
        )
        assert result == "Office Entertainment"


class TestRuleOrderBugFixed:
    """The central P1-E fix (docs/invoice-ocr-plan.md §24): the previous
    "first category in a fixed priority list with any keyword hit wins"
    implementation always returned "Office Entertainment" for a bakery
    vendor whose own item description said "Cake", even though "cake" is
    itself an Employee-Care-specific keyword the old fixed order never
    reached — real, live evidence for a farewell/celebration purchase,
    outranked only by an arbitrary list position, not by weaker evidence.
    Found live on a real document; reproduced synthetically here."""

    def test_a_specific_occasion_keyword_outranks_a_generic_venue_type_keyword(self) -> None:
        result = classify_category(
            vendor_name="Riverside Bakeshop", item_descriptions=["Chocolate Cake"], filename=None,
        )
        assert result == "Employee Care"

    def test_the_venue_type_keyword_alone_with_no_occasion_word_is_unaffected(self) -> None:
        """Regression guard: a bakery purchase with no occasion word at
        all must still resolve to Office Entertainment (its own
        established, correct default) — this fix only ever changes the
        outcome when genuine competing evidence for a different category
        exists, never removes the bakery's own base coverage."""
        assert classify_category(vendor_name="Riverside Bakeshop", item_descriptions=["Chocolate Loaf"], filename=None) == "Office Entertainment"


class TestWeakEvidenceCanNeverOutaccumulateAStrongSignal:
    """P1-E.1: self-caught during this phase's own test-writing — a plain
    linear multi-term bonus applied to WEAK matches let enough repeated
    generic venue words (three or more) accumulate past a single genuine
    STRONG hit from a rival category, contradicting this module's own
    stated design invariant. Fixed with a converging bonus; this is the
    direct regression guard for that invariant, independent of any one
    real document."""

    def test_many_weak_matches_still_lose_to_a_single_strong_match(self) -> None:
        result = classify_category(
            vendor_name=None, item_descriptions=[],
            filename=None,
            raw_text=(
                "restaurant cafe coffee catering snack food court pizza bakeshop bake shop bakery "
                "and one single cake"
            ),
        )
        assert result == "Employee Care"


class TestMultipleSupportingTerms:
    def test_several_matching_terms_in_the_same_category_accumulate(self) -> None:
        result = classify_category(
            vendor_name="Generic Traders", item_descriptions=["Pens", "Folders", "Stapler", "Printer Ink"],
            filename=None,
        )
        assert result == "Stationary / Others"


class TestGenericFinancialWordsAreNeverCategoryEvidence:
    """Phase 8's own explicit requirement: generic financial/document
    vocabulary must never itself produce a category — none of these words
    appear in _CATEGORY_KEYWORDS at all; this is a regression guard should
    one ever be added by mistake."""

    @pytest.mark.parametrize("word", ["invoice", "total", "tax", "payment", "amount", "receipt"])
    def test_bare_financial_vocabulary_produces_no_category(self, word) -> None:
        assert classify_category(vendor_name=None, item_descriptions=[word], filename=None) is None


class TestRawTextProvidesProseEvidence:
    """P1-E.1 (docs/invoice-ocr-plan.md §25): raw_text carries the
    document's own full OCR text — real category evidence a document
    states only in free prose (a cash-receipt voucher's own "received
    amount Rs. 6,000/- as Salary/ Stipend") never reaches vendor_name or a
    structured line-item description at all. Synthetic reproductions of
    the real gap found live, not the real documents' own exact wording.

    raw_text is deliberately a *fallback* source (see classify_category's
    own docstring for the real Golden Dataset case — a company's own
    idiosyncratic bookkeeping convention — that justified this precedence,
    found live during this phase's own benchmark re-run), consulted only
    when vendor_name/item_descriptions/filename together produce no
    category evidence at all."""

    def test_a_prose_sentence_provides_category_evidence_when_no_other_source_does(self) -> None:
        result = classify_category(
            vendor_name="STIXOR", item_descriptions=[], filename=None,
            raw_text="STIXOR Cash Receipt Mr/Ms Ifra Jamil received amount Rs. 6,000/- as Salary/ Stipend",
        )
        assert result == "Salary"

    def test_a_different_prose_keyword_also_works(self) -> None:
        result = classify_category(
            vendor_name="STIXOR", item_descriptions=[], filename=None,
            raw_text="STIXOR Cash Receipt received amount Rs.500 for office cleaning this month",
        )
        assert result == "Office / Misc Supplies"

    def test_raw_text_absent_is_unaffected(self) -> None:
        """A strong vendor/item-description signal must still work exactly
        as before when raw_text is simply absent (None)."""
        assert classify_category(vendor_name="KFC Gulberg", item_descriptions=[], filename=None, raw_text=None) == "Office Entertainment"

    def test_raw_text_alone_with_no_vendor_or_items_still_works(self) -> None:
        result = classify_category(
            vendor_name=None, item_descriptions=[], filename=None,
            raw_text="Paid for petrol at the filling station today",
        )
        assert result == "Vehicle Running & Maintenance"

    def test_generic_financial_language_in_raw_text_is_never_evidence(self) -> None:
        """Phase 8's own explicit requirement, now proven against a full
        prose document rather than just a bare word — an ordinary
        invoice's own boilerplate (amounts, totals, tax, payment terms)
        must never itself produce a category."""
        result = classify_category(
            vendor_name="Unrelated Traders Pvt Ltd", item_descriptions=[], filename=None,
            raw_text="Invoice Total: Rs. 5,000.00 Tax: Rs. 500.00 Amount Due: Rs. 5,500.00 Payment received, thank you for your receipt",
        )
        assert result is None

    def test_a_structured_answer_is_returned_outright_without_even_consulting_raw_text(self) -> None:
        """The central P1-E.1 precedence fix: once vendor_name/item_
        descriptions alone already reach a confident answer, raw_text is
        never consulted at all — even a raw_text signal that would
        otherwise win on its own (here, "salary", strong) must not
        override an already-sufficient structured answer."""
        result = classify_category(
            vendor_name="Riverside Bakeshop", item_descriptions=["Chocolate Cake"], filename=None,
            raw_text="paid as a token of appreciation, part of this month's salary run",
        )
        assert result == "Employee Care"

    def test_raw_text_fallback_only_fires_when_structured_evidence_is_silent(self) -> None:
        result = classify_category(
            vendor_name="Unrelated Traders Pvt Ltd", item_descriptions=[], filename=None,
            raw_text="Visited the restaurant then paid for petrol at the filling station",
        )
        # No structured evidence at all -> raw_text is consulted -> "petrol"
        # (strong, Vehicle Running & Maintenance) outranks "restaurant"
        # (weak, Office Entertainment).
        assert result == "Vehicle Running & Maintenance"

    def test_a_genuine_structured_tie_is_not_arbitrarily_broken_by_raw_text(self) -> None:
        """A real ambiguity *within* the structured evidence itself must
        stay unresolved — raw_text is not consulted to break a tie it had
        no part in creating."""
        result = classify_category(
            vendor_name="Restaurant & Florist Complex", item_descriptions=[], filename=None,
            raw_text="thank you for your order, please visit us again soon",
        )
        assert result is None

    def test_raw_text_can_still_produce_a_genuine_tie_of_its_own(self) -> None:
        result = classify_category(
            vendor_name=None, item_descriptions=[], filename=None,
            raw_text="Visited the restaurant then stopped by the florist on the way back",
        )
        assert result is None

    def test_a_weak_only_fallback_match_with_no_structured_evidence_at_all_is_not_confident(self) -> None:
        """Found live: a bakery vendor recovered *only* via raw_text
        (structured vendor extraction had failed on the real document)
        landed on "Office Entertainment" via "bakeshop" alone — the same
        already-acknowledged, genuine ambiguity a bakery purchase's own
        category always carries (routine snack vs. a specific occasion)
        when nothing else disambiguates it. A single weak keyword, with
        vendor_name/item_descriptions contributing nothing at all, is not
        enough to confidently decide it."""
        result = classify_category(
            vendor_name=None, item_descriptions=[], filename=None,
            raw_text="Receipt from Riverside Bakeshop, thank you for your order",
        )
        assert result is None

    def test_a_weak_fallback_match_confirmed_by_a_strong_one_is_still_confident(self) -> None:
        """The same fallback path, but with a specific occasion word
        alongside the generic venue word — real, sufficient evidence, not
        merely a vendor-type guess."""
        result = classify_category(
            vendor_name=None, item_descriptions=[], filename=None,
            raw_text="Receipt from Riverside Bakeshop for a birthday cake",
        )
        assert result == "Employee Care"


class TestAmbiguousEvidenceIsReviewNotAnArbitraryPick:
    """Phase 9's own explicit requirement: when two categories' own
    evidence is genuinely, closely tied, the classifier must not guess."""

    def test_two_weak_single_keyword_categories_within_the_ambiguity_margin_return_none(self) -> None:
        # Both "restaurant" (Office Entertainment, weak) and "florist"
        # (Employee Care, weak) score identically (5.0 each) with no
        # tie-breaking evidence either way — a genuine, unresolvable tie.
        result = classify_category(vendor_name="Restaurant & Florist Complex", item_descriptions=[], filename=None)
        assert result is None
