"""P1 — vendor's own application of the shared candidate-selection
architecture (candidates.py). test_fields.py's TestFindVendorName/
TestVendorFallback* classes cover the stable, legacy `find_vendor_name`
4-tuple contract and must keep passing unchanged (they do — see this
phase's own regression report); this file covers what's actually NEW:
`find_vendor_candidate_selection`'s richer SelectionResult (evidence,
ambiguity, provenance) and the new evidence dimensions (font size,
known-vendor matching as a scoring input rather than a post-hoc-only
boost).

Every case here is synthetic and general — the task's own explicit
instruction not to hardcode the 12 real failing Golden Dataset documents.
"""
from ocr import PositionedWord

from invoice_extraction.fields import (
    find_vendor_candidate_selection, find_vendor_name, vendor_candidate_cleared_confidence_floor,
)
from invoice_extraction.lines import group_into_lines


def _word(
    text: str, x0: float, y0: float, page: int = 0, confidence: float = 1.0, font_size: float | None = None,
) -> PositionedWord:
    return PositionedWord(
        text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page,
        confidence=confidence, font_size=font_size,
    )


class TestCase1_AProminentVendorScoresStrongly:
    def test_a_clean_top_of_page_vendor_is_a_confident_accept(self) -> None:
        words = [
            _word("Express", 50, 50), _word("Mart", 110, 50),
            _word("Total: 270", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner is not None
        assert result.winner.value == "Express Mart"
        assert result.status == "accept"
        assert result.winner.evidence["ocr_confidence"] > 0


class TestCase2_LeadingReferenceStampStillStrips:
    """Existing fix (docs/invoice-ocr-plan.md §18) must survive the P1
    refactor unchanged."""

    def test_a_leading_amount_stamp_is_still_stripped_via_the_new_path(self) -> None:
        words = [
            _word("Rs1100", 50, 50), _word("Express", 130, 50), _word("Mart", 210, 50),
            _word("Total: 1100", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Express Mart"


class TestCase3_NTNTruncationStillWorks:
    def test_an_ntn_number_is_still_truncated_via_the_new_path(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50), _word("NTN#0819531-5", 240, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"


class TestCase4_AProductDescriptionDoesNotBeatARealHeaderVendor:
    def test_a_line_item_description_within_the_top_window_does_not_outrank_the_real_vendor(self) -> None:
        """A short receipt where a line-item description happens to fall
        inside the top-25%-of-page candidate window must not beat the
        actual header vendor printed above it — document order plus equal
        confidence should keep the earlier, real vendor line on top."""
        words = [
            _word("Riverside", 50, 20), _word("Grocers", 150, 20),
            _word("Cooking", 50, 60), _word("Oil", 140, 60), _word("5L", 180, 60),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Grocers"

    def test_a_known_vendor_match_does_not_drag_a_genuinely_weaker_candidate_above_a_strong_one(self) -> None:
        """Known-vendor matching is one signal among several (P1 §15),
        never an unconditional override: a low-confidence table-row
        candidate (the messy, sub-floor common shape of a line-item
        description) must not beat a cleanly-printed, floor-clearing
        header vendor just because it happens to fuzzy-match a known
        vendor name — the floor-tier gap (~4.0 at these constants) is far
        larger than the known-vendor bonus (0.3) can ever close."""
        words = [
            _word("Riverside", 50, 20, confidence=0.95), _word("Grocers", 150, 20, confidence=0.95),
            _word("Cooking", 50, 60, confidence=0.6), _word("Oil", 140, 60, confidence=0.6),
            _word("Traders", 190, 60, confidence=0.6),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines, known_vendors=["Cooking Oil Traders"])
        assert result.winner.value == "Riverside Grocers"

    def test_a_known_vendor_match_correctly_breaks_a_genuine_tie(self) -> None:
        """The flip side of the guard above: when two candidates are
        otherwise identically strong (same confidence, both clear the
        floor), a company's own known-vendor history is exactly the kind
        of real, if soft, evidence that should decide between them —
        this is a feature of the new architecture, not something to
        suppress."""
        words = [
            _word("Riverside", 50, 20), _word("Grocers", 150, 20),
            _word("Cooking", 50, 60), _word("Oil", 140, 60), _word("Traders", 190, 60),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines, known_vendors=["Cooking Oil Traders"])
        assert result.winner.value == "Cooking Oil Traders"


class TestCase5_AnAccountHolderFieldIsNotMistakenForTheVendor:
    """A new, general negative-evidence signal (P1) — a bank statement/
    invoice recipient label is never the party being paid."""

    def test_an_account_holder_label_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Account", 50, 50), _word("Holder:", 110, 50), _word("Ayesha", 190, 50), _word("Khan", 250, 50),
            _word("Riverside", 50, 100), _word("Bank", 130, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Bank"

    def test_account_title_variant_is_also_recognized(self) -> None:
        words = [
            _word("Account", 50, 50), _word("Title:", 110, 50), _word("Zaman", 190, 50), _word("Traders", 260, 50),
            _word("Northside", 50, 100), _word("Exchange", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Northside Exchange"


class TestCase6_GenuineAmbiguityIsReviewNotAnArbitraryPick:
    def test_two_near_identical_weak_candidates_are_flagged_for_review(self) -> None:
        """Neither candidate clears the confidence floor, and their scores
        land within the ambiguity margin of each other — a real winner is
        still named (vendor's own established never-return-nothing
        guarantee), but the outcome is explicitly REVIEW, not a confident
        ACCEPT dressed up as one."""
        words = [
            _word("Stamp-A", 50, 50, confidence=0.60), _word("Stamp-B", 50, 100, confidence=0.601),
            _word("Total: 500", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.status == "review"
        assert result.winner is not None  # still names a winner — recall is preserved
        # The legacy 4-tuple contract keeps working identically underneath.
        legacy = find_vendor_name(lines)
        assert legacy is not None
        assert legacy[0] == result.winner.value

    def test_a_clear_confidence_gap_is_never_flagged_as_ambiguous(self) -> None:
        """Regression guard: the ambiguity margin must not be so generous
        that it starts flagging ordinary, unambiguous wins."""
        words = [
            _word("Stamp-9", 50, 50, confidence=0.6),
            _word("Riverside", 50, 100, confidence=0.83), _word("Bakery", 150, 100, confidence=0.83),
            _word("Total: 500", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.status == "accept"


class TestFontSizeEvidence:
    """font_size is declared on PositionedWord but not yet populated by any
    real OCR/PDF path in this codebase (verified: no `font_size=` call
    site exists outside its own declaration) — these tests exercise the
    architecture directly with synthetic font sizes, honestly proving the
    *mechanism* works, not that it currently changes real-document
    behavior (it can't, until a future OCR/PDF change starts reporting
    real values)."""

    def test_a_larger_font_size_contributes_positive_evidence(self) -> None:
        words = [
            _word("Riverside", 50, 50, font_size=18.0), _word("Traders", 150, 50, font_size=18.0),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert "font_size" in result.winner.evidence
        assert result.winner.evidence["font_size"] > 0

    def test_no_font_size_data_contributes_no_signal_at_all(self) -> None:
        """The honest, current-reality case for every document this
        pipeline can actually see today."""
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert "font_size" not in result.winner.evidence

    def test_font_size_can_tip_a_genuine_tie_between_two_floor_clearing_candidates(self) -> None:
        """The one thing the old position-only fallback could never do:
        let a real signal other than confidence/order decide between two
        candidates that are otherwise equally strong."""
        words = [
            _word("Small", 50, 20, confidence=0.9, font_size=8.0), _word("Print", 120, 20, confidence=0.9, font_size=8.0),
            _word("Riverside", 50, 60, confidence=0.9, font_size=24.0), _word("Traders", 150, 60, confidence=0.9, font_size=24.0),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"


class TestKnownVendorEvidence:
    def test_a_known_vendor_fuzzy_match_is_recorded_as_its_own_evidence_signal(self) -> None:
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines, known_vendors=["Riverside Traders Pvt Ltd"])
        assert "known_vendor_match" in result.winner.evidence
        assert result.winner.evidence["known_vendor_match"] > 0

    def test_no_known_vendors_supplied_contributes_no_signal(self) -> None:
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert "known_vendor_match" not in result.winner.evidence

    def test_known_vendor_matching_is_soft_evidence_not_an_unconditional_override(self) -> None:
        """A known-vendor bonus must never be large enough to drag a
        genuinely rejected (reference-number-shaped) candidate above a
        real, clean vendor name — soft evidence, never an override."""
        words = [
            _word("Rs1100", 50, 50),
            _word("Northside", 50, 100), _word("Hardware", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        # "Rs1100" itself will never fuzzy-match anything real, but this
        # proves the mechanism can't be abused even in principle: a known-
        # vendor bonus is capped well below the rejection penalty.
        result = find_vendor_candidate_selection(lines, known_vendors=["Northside Hardware"])
        assert result.winner.value == "Northside Hardware"


class TestP1BCurrencyStampToleratesOCRDigitCorruption:
    """P1-B Golden Dataset error analysis: a leading amount stamp whose
    digits got partially OCR'd into letters ("RsS40") dilutes the old
    digit-ratio check below the 0.5 threshold, letting the stamp ride along
    into the vendor text. Detecting the currency prefix itself — never
    itself part of a real business's own name — closes this independently
    of how many digits after it survived OCR intact."""

    def test_a_currency_prefixed_stamp_with_ocr_corrupted_digits_is_still_stripped(self) -> None:
        words = [
            _word("RsS40", 50, 50), _word("Express", 130, 50), _word("Mart", 220, 50),
            _word("Total: 540", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Express Mart"

    def test_a_dollar_prefixed_stamp_is_also_stripped(self) -> None:
        words = [
            _word("$40", 50, 50), _word("Riverside", 130, 50), _word("Traders", 230, 50),
            _word("Total: 40", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"


class TestP1BTrailingLabelFragmentIsTruncated:
    """P1-B Golden Dataset error analysis: OCR grouped a "Date:" label
    (misread as "ate:", the leading "D" lost) onto the very *same* visual
    row as the real vendor name — one single OCR line reading "Express
    Mart ate:" end to end, not two separate rows a continuation-merge would
    need to combine. A trailing colon always marks the start of a new
    field's own label, regardless of which label it is or how badly OCR
    mangled its spelling, so this generalizes past this one document."""

    def test_a_same_line_trailing_label_fragment_is_truncated(self) -> None:
        words = [
            _word("Express", 50, 50), _word("Mart", 130, 50), _word("ate:", 210, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Express Mart"

    def test_a_business_name_is_never_reduced_to_nothing_by_this(self) -> None:
        """Drops at most the line's own last word — a (implausible) name
        that is itself only a single colon-terminated word survives
        unchanged rather than being discarded entirely. "Acme:" (not a
        recognized label word) keeps this scoped to the trailing-fragment
        truncation itself, clear of the unrelated label-anchored path."""
        words = [_word("Acme:", 50, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Acme:"


class TestP1BLabelFragmentContinuationIsAlsoRejected:
    """A separate, still-general guard for the two-*line* shape of the
    same underlying problem — a label fragment glued onto its own row
    directly below the vendor, rather than onto the vendor's own row: never
    observed live in this dataset (the real case above turned out to be
    single-line), but the identical reasoning (a trailing colon marks a
    label, never a business-name suffix) applies just as well to a
    continuation candidate."""

    def test_a_colon_terminated_next_line_is_not_merged_as_a_continuation(self) -> None:
        words = [
            _word("Express", 50, 50), _word("Mart", 130, 50),
            _word("ate:", 50, 66),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Express Mart"

    def test_a_genuine_two_line_continuation_without_a_colon_still_merges(self) -> None:
        """Regression guard: the new colon check must not affect the
        ordinary, already-working continuation case."""
        words = [
            _word("Zaman", 50, 50), _word("Traders", 130, 50),
            _word("Hardware", 50, 66), _word("Store", 150, 66),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Zaman Traders Hardware Store"


class TestP1BEmailAddressIsRejectedAsAVendorCandidate:
    """P1-B Golden Dataset error analysis: a delivery-platform receipt's
    own support/no-reply contact address, printed near the top, beat the
    real vendor name for lack of any structural reason to reject it. A
    business's own display name is never itself formatted as an email
    address, in any country or industry."""

    def test_an_email_address_line_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("orders@somedeliveryplatform.com", 50, 50),
            _word("Riverside", 50, 100), _word("Diner", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Diner"

    def test_a_business_name_that_merely_contains_an_at_sign_is_not_rejected(self) -> None:
        """The email check matches the candidate's *entire* cleaned text
        against a strict local-part@domain.tld shape — a business legitimately
        styled with an "@" (e.g. a social-media-style handle in its own
        name) that isn't actually a full email address must survive."""
        words = [_word("Eat@Riverside", 50, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Eat@Riverside"


class TestP1BGreetingSalutationIsRejectedAsAVendorCandidate:
    """P1-B Golden Dataset error analysis: once an email-address candidate
    is correctly rejected, "Dear Muhammad," (a personal salutation printed
    just below it, addressed to the document's own recipient) was the next
    thing that would otherwise win. Universal across any letter/email
    format, never a business's own display name."""

    def test_a_dear_salutation_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Dear", 50, 50), _word("Muhammad,", 110, 50),
            _word("Riverside", 50, 100), _word("Diner", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Diner"

    def test_a_business_name_that_merely_contains_dear_midline_is_not_rejected(self) -> None:
        """The salutation check anchors at the start of the line, not a
        substring anywhere in it."""
        words = [_word("Oh", 50, 50), _word("Deary", 90, 50), _word("Me", 160, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Oh Deary Me"


class TestP1BBareDocumentTypeLabelIsRejectedAsAVendorCandidate:
    """P1-B Golden Dataset error analysis: a standalone "Cash Receipt"
    line — the document announcing its own type, nothing else — was
    returned as the vendor two lines above the real business name.
    _strip_leading_document_type_label already handles this vocabulary
    glued onto the *front* of a longer line; this covers the line
    consisting of nothing else at all."""

    def test_a_bare_document_type_label_line_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Cash", 50, 50), _word("Receipt", 110, 50),
            _word("Riverside", 50, 100), _word("Traders", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"

    def test_the_same_label_glued_onto_a_longer_line_is_still_only_stripped_not_rejected(self) -> None:
        """Regression guard: _strip_leading_document_type_label's existing
        behavior (relieve a real name of the prefix, don't reject the
        whole line) must be unaffected by the new bare-line check."""
        words = [_word("INVOICE", 50, 50), _word("Sunrise", 200, 50), _word("Traders", 280, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Sunrise Traders"


class TestP1BALoneSubFloorCandidateIsFlaggedLowConfidence:
    """P1-B Golden Dataset error analysis: select_candidate's own lone-
    candidate rule is an unconditional accept, by design — it answers
    "did this beat a rival", never "is this trustworthy on its own terms".
    A garbled corner stamp that was the *only* thing OCR recovered in the
    header region (found live: "BiQ-13", on a receipt whose real vendor
    name was apparently never extracted at all) needs that second question
    answered too."""

    def test_a_lone_sub_floor_candidate_is_flagged(self) -> None:
        words = [_word("BiQ-13", 50, 50, confidence=0.6), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.status == "accept"  # still names a winner — recall preserved
        assert vendor_candidate_cleared_confidence_floor(result.winner) is False

    def test_a_lone_floor_clearing_candidate_is_not_flagged(self) -> None:
        words = [
            _word("Riverside", 50, 50, confidence=0.95), _word("Traders", 150, 50, confidence=0.95),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert vendor_candidate_cleared_confidence_floor(result.winner) is True

    def test_a_label_anchored_winner_is_always_treated_as_cleared(self) -> None:
        words = [_word("Sold", 50, 50), _word("By:", 90, 50), _word("Ayesha", 130, 50), _word("Khan", 190, 50)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.method == "label_anchor+pattern"
        assert vendor_candidate_cleared_confidence_floor(result.winner) is True


class TestP1BAddressLineIsRejectedAsAVendorCandidate:
    """P1-B Golden Dataset error analysis: a street/building address,
    printed as the top-of-page candidate ahead of the real vendor name, was
    returned whole ("Plaza 229,Third floor, Hotel view square, Bahria
    Spring", found live) — no structural signal previously distinguished it
    from a real business name."""

    def test_an_address_line_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Plaza", 50, 50), _word("229,", 120, 50), _word("Third", 170, 50), _word("Floor", 240, 50),
            _word("Riverside", 50, 100), _word("Traders", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"

    def test_a_comma_punctuated_business_name_without_address_vocabulary_is_not_rejected(self) -> None:
        """The address check requires both a comma AND a recognizable
        address-structure word — a real business legitimately punctuated
        with a comma ("7-Eleven, Inc.") must survive unaffected."""
        words = [_word("7-Eleven,", 50, 50), _word("Inc.", 130, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "7-Eleven, Inc."


class TestP1BExpandedVendorPositiveLabels:
    """P1-B: "supplier"/"merchant"/"billed by" are the same universal,
    country/industry-agnostic invoice-header vocabulary as the pre-existing
    "bill from"/"sold by"/"from"/"vendor" labels."""

    def test_supplier_label_is_recognized(self) -> None:
        words = [
            _word("Supplier:", 50, 50), _word("Riverside", 150, 50), _word("Traders", 250, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Traders"
        assert result.winner.method == "label_anchor+pattern"

    def test_merchant_label_is_recognized(self) -> None:
        words = [
            _word("Merchant:", 50, 50), _word("Northside", 150, 50), _word("Exchange", 260, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Northside Exchange"


class TestP1BExpandedCustomerNegativeLabels:
    """P1-B: "bill to"/"ship to" reuse the identical semantic role
    (who a document's amount is FOR) LABEL_VARIANTS' own "customer_label"
    already recognizes elsewhere in this module, applied here as a vendor
    rejection signal — never the vendor's own wording."""

    def test_a_bill_to_line_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Bill", 50, 50), _word("To:", 100, 50), _word("Ayesha", 150, 50), _word("Khan", 220, 50),
            _word("Riverside", 50, 100), _word("Bakery", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Riverside Bakery"

    def test_a_ship_to_line_is_skipped_for_the_real_vendor_below(self) -> None:
        words = [
            _word("Ship", 50, 50), _word("To:", 100, 50), _word("Warehouse", 150, 50), _word("3", 260, 50),
            _word("Northside", 50, 100), _word("Hardware", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Northside Hardware"


class TestProvenanceIsExplainable:
    def test_the_winning_candidate_exposes_which_evidence_produced_its_score(self) -> None:
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.score == sum(result.winner.evidence.values())
        assert "ocr_confidence" in result.winner.evidence

    def test_a_label_anchored_match_is_still_the_strongest_tier_and_skips_scoring_entirely(self) -> None:
        words = [
            _word("Total", 50, 20),  # would-be positional bait, higher on the page
            _word("Sold", 50, 60), _word("By:", 90, 60), _word("Ayesha", 130, 60), _word("Khan", 190, 60),
        ]
        lines = group_into_lines(words)
        result = find_vendor_candidate_selection(lines)
        assert result.winner.value == "Ayesha Khan"
        assert result.winner.method == "label_anchor+pattern"
        assert result.status == "accept"
        # Only one candidate ever existed for this path — the noise-
        # filter/scoring machinery never runs for a label match at all.
        assert len(result.candidates) == 1
