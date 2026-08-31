"""Rule 3.1 — label-anchored extraction, direct unit coverage of the
mechanism itself (see test_extract_invoice_real_documents.py for the real
documents that originally surfaced the two bugs these tests guard)."""
from ocr import PositionedWord

from invoice_extraction.fields import find_date_anywhere, find_label_anchored_text, find_total_anywhere, find_vendor_name
from invoice_extraction.lines import group_into_lines


def _word(text: str, x0: float, y0: float, page: int = 0, confidence: float = 1.0) -> PositionedWord:
    return PositionedWord(
        text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page, confidence=confidence,
    )


class TestSameLineExtraction:
    def test_label_colon_value_on_one_line(self) -> None:
        words = [_word("Invoice", 50, 100), _word("No:", 100, 100), _word("INV-1841", 140, 100)]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "invoice_number")
        assert found is not None
        assert found[0] == "INV-1841"

    def test_a_large_gap_before_a_right_aligned_value_is_still_accepted(self) -> None:
        """Regression guard for the real 'Total ... $100.00' case — a large
        visual gap on the same line does not by itself mean the value
        belongs to a different column."""
        words = [_word("Total", 50, 100), _word("$183,254.00", 450, 100)]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "total")
        assert found is not None
        assert found[0] == "$183,254.00"


class TestCustomerLabel:
    """LABEL_VARIANTS['customer_label'] existed but was never wired into
    extract_invoice.py until this — same lookup mechanism as every other
    label-anchored field, just verifying it actually resolves."""

    def test_bill_to_label_resolves_the_customer_name(self) -> None:
        words = [_word("Bill", 50, 100), _word("To:", 90, 100), _word("Acme", 140, 100), _word("Corp", 180, 100)]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "customer_label")
        assert found is not None
        assert found[0] == "Acme Corp"

    def test_no_customer_label_present_is_not_found(self) -> None:
        words = [_word("Vendor:", 50, 100), _word("ABC", 100, 100), _word("Traders", 140, 100)]
        lines = group_into_lines(words)
        assert find_label_anchored_text(lines, "customer_label") is None


class TestNextLineFallback:
    def test_label_only_line_reads_the_value_from_the_line_below(self) -> None:
        words = [
            _word("Invoice", 50, 100), _word("#:", 90, 100),
            _word("BK-20260205-002", 50, 118),
        ]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "invoice_number")
        assert found is not None
        assert found[0] == "BK-20260205-002"

    def test_only_the_column_aligned_with_the_label_is_taken_not_the_whole_next_line(self) -> None:
        """Regression guard for the real 'BILL FROM: / muhammad haroon
        M180362221 SGD' case — a neighboring column's value on the same
        visual row as the real value must not be swept in."""
        words = [
            _word("Bill", 50, 100), _word("From:", 90, 100),
            _word("Invoice", 400, 100), _word("No:", 450, 100),
            _word("Ayesha", 50, 118), _word("Khan", 100, 118),
            _word("INV-42", 400, 118),
        ]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "vendor_label")
        assert found is not None
        assert found[0] == "Ayesha Khan"


class TestPackedMultiColumnHeaderRow:
    def test_a_labels_only_row_correctly_yields_no_same_line_value(self) -> None:
        """Three labels packed onto one row with no values on that row at
        all — the same-line path must not grab the next label's text as if
        it were this field's value."""
        words = [
            _word("Bill", 50, 100), _word("From:", 90, 100),
            _word("Invoice", 400, 100), _word("No:", 450, 100),
            _word("Currency", 550, 100),
        ]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "vendor_label")
        # Nothing usable on the next line either in this minimal example —
        # the important assertion is that it did NOT return "Invoice No:
        # Currency" as if that were the vendor.
        assert found is None or found[0] not in ("Invoice No: Currency", "Invoice No:")


class TestFindVendorName:
    def test_a_labeled_vendor_is_preferred_over_positional_guessing(self) -> None:
        words = [
            _word("Total", 50, 50),  # would-be positional-fallback bait, higher on the page
            _word("Sold", 50, 100), _word("By:", 90, 100),
            _word("Ayesha", 50, 118), _word("Khan", 100, 118),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "Ayesha Khan"
        assert method == "label_anchor+pattern"

    def test_no_label_falls_back_to_the_topmost_text_on_the_page(self) -> None:
        words = [_word("ABC Traders", 50, 50), _word("Invoice", 50, 200), _word("Total: 100", 50, 400)]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "ABC Traders"
        assert method == "positional_fallback"

    def test_a_low_confidence_topmost_line_is_skipped_for_a_clean_one_below(self) -> None:
        """Regression guard for a real bug found live scanning 35 real
        Pakistani petty-cash receipts: every single one had a handwritten
        filing annotation ("Bill-4", garbled by OCR into things like
        "Bial-1") stamped in the corner, OCR'd at low confidence, and it
        was consistently the topmost line — so the plain "first line"
        fallback picked it as the vendor name on all 35, never the actual
        business name a line or two below (OCR'd at 0.9+ on the same
        receipts)."""
        words = [
            _word("Bial-1", 50, 50, confidence=0.63),
            _word("DHA", 50, 100, confidence=0.92), _word("Filling", 100, 100, confidence=0.92),
            _word("Station", 160, 100, confidence=0.92),
            _word("Total: 400", 50, 1000, confidence=0.9),  # tall receipt, keeps both lines above top_fraction's cutoff
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "DHA Filling Station"
        assert method == "positional_fallback"

    def test_a_reference_number_line_is_skipped_even_at_high_confidence(self) -> None:
        """Regression guard: Pakistani FBR-integrated POS receipts print a
        mandatory e-invoice reference number as the very first line, ahead
        of the business name, and OCR reads it just as cleanly as anything
        else on the receipt — so confidence alone can't catch this one."""
        words = [
            _word("FBRInvoice#.:147160FFLP23522851", 50, 50, confidence=0.97),
            _word("Layers", 50, 100, confidence=0.99), _word("Bakeshop", 110, 100, confidence=0.99),
            _word("Total: 2200", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "Layers Bakeshop"

    def test_nothing_clears_the_bar_still_returns_the_first_line_not_nothing(self) -> None:
        """When every candidate line looks like noise, the plain first-line
        guess is still returned rather than losing the field entirely — a
        low-quality guess beats no vendor name at all."""
        words = [
            _word("B0-9", 50, 50, confidence=0.49), _word("Rs1100", 50, 100, confidence=0.7),
            _word("Total: 1100", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "B0-9"
        assert method == "positional_fallback"


class TestFindDateAnywhere:
    """Rule 3.4's vendor-name positional-fallback treatment applied to
    invoice_date — see fields.find_date_anywhere's own docstring for the
    two real receipt shapes this recovers."""

    def test_a_bare_date_with_no_label_anywhere_on_the_page(self) -> None:
        words = [_word("Express", 50, 50, confidence=0.99), _word("Mart", 110, 50, confidence=0.99)]
        words += [_word("16", 50, 100, confidence=0.92), _word("Jun", 90, 100, confidence=0.92), _word("2026", 130, 100, confidence=0.92)]
        lines = group_into_lines(words)
        found = find_date_anywhere(lines)
        assert found is not None
        text, page, bbox = found
        assert text == "16 Jun 2026"

    def test_a_date_glued_onto_its_own_label_is_still_found(self) -> None:
        words = [_word("Date:29/06/2026.16:29:41", 50, 100, confidence=0.96)]
        lines = group_into_lines(words)
        found = find_date_anywhere(lines)
        assert found is not None

    def test_a_low_confidence_date_shaped_stamp_is_skipped(self) -> None:
        """A garbled low-confidence line that happens to be date-shaped
        should not be preferred over nothing when it's the only candidate —
        conservative by design, same as the vendor fallback's own
        confidence floor."""
        words = [_word("12/34/5678", 50, 50, confidence=0.4)]
        lines = group_into_lines(words)
        assert find_date_anywhere(lines) is None

    def test_no_date_anywhere_is_none(self) -> None:
        words = [_word("Total", 50, 50, confidence=0.99), _word("400.00", 150, 50, confidence=0.99)]
        lines = group_into_lines(words)
        assert find_date_anywhere(lines) is None


class TestSynonymVariantsDoNotCascade:
    def test_a_shorter_overlapping_synonym_does_not_re_anchor_on_the_same_line(self) -> None:
        """Regression guard for a real bug found live. A "BILLED TO"
        heading sits beside a "PAYMENT & SHIPPING" heading, with the real
        customer name two rows below. The specific "billed to" variant
        matched correctly but produced no value, and matching then fell
        through to the field's own bare "to" synonym — which re-anchored
        on the *second word of the same label*, shifting the next-line
        lookup right by one column and returning the neighbouring
        sub-heading ("NAME") as the customer's name. Once a line's label
        is matched, its shorter synonyms must not get a second attempt at
        the same words."""
        words = [
            _word("BILLED", 80, 100), _word("TO", 160, 100),
            _word("PAYMENT", 610, 100), _word("SHIPPING", 700, 100),
            _word("CUSTOMER", 82, 140), _word("NAME", 185, 140), _word("ADDRESS", 277, 140),
            _word("Veronica", 80, 180), _word("Costello", 150, 180),
        ]
        lines = group_into_lines(words)
        found = find_label_anchored_text(lines, "customer_label")
        assert found is not None
        assert found[0].startswith("Veronica Costello")
        assert found[0] != "NAME"


class TestSkipLines:
    def test_a_skipped_row_is_not_matched_as_a_label_row(self) -> None:
        """The line-item table's "SUBTOTAL" column header is the same word
        as the totals-section "Subtotal" label and comes first on the page,
        so without excluding the header row a totals lookup anchors there
        and reads the first line item's amount instead. Real regression,
        caught live once row grouping improved enough to read the header
        row as one row."""
        words = [
            _word("ITEMS", 79, 100), _word("QTY", 502, 100), _word("SUBTOTAL", 823, 100),
            _word("Widget", 79, 140), _word("1", 502, 140), _word("$43.00", 844, 140),
            _word("SUBTOTAL", 539, 180), _word("$93.00", 843, 180),
        ]
        lines = group_into_lines(words)

        assert find_label_anchored_text(lines, "subtotal")[0] == "$43.00"
        assert find_label_anchored_text(lines, "subtotal", skip_lines={0})[0] == "$93.00"


class TestVendorFallbackSkipsADateLine:
    def test_a_date_issued_header_line_is_not_mistaken_for_a_vendor_name(self) -> None:
        """Regression guard for a real bug found live on a real native-PDF
        billing statement: "Date issued Jul 28, 2026 Jul 28, 2026" has a
        roughly even letter/digit mix (the digit-ratio filter alone lets it
        through) and native PDF text is always 1.0 confidence (so the
        confidence filter can't catch it either) — it was picked as the
        vendor ahead of the real one two lines below."""
        words = [
            _word("Date", 50, 50), _word("issued", 90, 50), _word("Jul", 150, 50),
            _word("28,", 180, 50), _word("2026", 210, 50),
            _word("Shopify", 50, 100), _word("Commerce", 110, 100), _word("Ltd.", 190, 100),
            _word("Total: 100", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        text, page, bbox, method = found
        assert text == "Shopify Commerce Ltd."


class TestVendorFallbackSkipsProviderCreditAndOrderMetadata:
    """General-pattern regression guards from the vendor-extraction golden-
    dataset audit (docs/invoice-ocr-plan.md §14). Every case below uses a
    synthetic vendor unrelated to the real dataset — these guard the
    *structural* rule (a POS/software self-credit line, a courtesy footer,
    an order-fulfillment field), not any one document."""

    def test_a_pos_software_credit_line_is_not_mistaken_for_the_vendor(self) -> None:
        words = [
            _word("Powered", 50, 50), _word("By", 100, 50), _word("SomeGenericPOS", 130, 50),
            _word("Riverside", 50, 100), _word("Diner", 140, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Diner"

    def test_a_courtesy_footer_printed_near_the_top_is_skipped(self) -> None:
        words = [
            _word("Thank", 50, 50), _word("you,", 90, 50), _word("please", 130, 50), _word("visit", 190, 50), _word("again.", 230, 50),
            _word("Northside", 50, 100), _word("Hardware", 150, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Northside Hardware"

    def test_an_order_fulfillment_field_is_not_mistaken_for_the_vendor(self) -> None:
        """A closed, industry-wide vocabulary ("Pickup"/"Delivery"/"Dine
        In") used by point-of-sale systems to record how an order was
        fulfilled — never a business's own name, regardless of which
        business issued the receipt."""
        words = [
            _word("Corner", 50, 50), _word("Cafe", 130, 50),
            _word("Pickup/Delivery", 50, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Corner Cafe"

    def test_a_real_vendor_name_containing_pickup_and_delivery_as_words_is_not_rejected(self) -> None:
        """The order-fulfillment check matches a candidate line's *entire*
        cleaned text, never a substring — a real business legitimately
        named around delivery service must not be caught by it."""
        words = [
            _word("City", 50, 50), _word("Pickup", 100, 50), _word("&", 160, 50), _word("Delivery", 180, 50), _word("Services", 260, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "City Pickup & Delivery Services"


class TestVendorFallbackStripsDocumentTypeLabel:
    """A generic document-title word ("Invoice") sometimes sits on the very
    same visual row as the vendor's own name in a hand-designed template —
    stripped from the front before the line is evaluated, never from
    mid-line."""

    def test_a_leading_invoice_label_glued_to_the_vendor_name_is_stripped(self) -> None:
        words = [
            _word("INVOICE", 50, 50), _word("Sunrise", 200, 50), _word("Traders", 280, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Sunrise Traders"

    def test_a_vendor_name_that_merely_contains_a_label_word_midline_is_untouched(self) -> None:
        words = [_word("Bill's", 50, 50), _word("Diner", 110, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Bill's Diner"


class TestVendorFallbackMergesATwoLineBusinessName:
    """A business name that wraps onto a second printed line — a proper
    name plus a business-type suffix on the very next row — is a layout
    shape, not a property of any one vendor."""

    def test_a_two_line_business_name_is_merged(self) -> None:
        words = [
            _word("Zaman", 50, 84), _word("Electric", 200, 84),
            _word("Hardware", 50, 100), _word("Traders", 150, 100),
            _word("Shop", 50, 130), _word("#", 110, 130), _word("14,", 130, 130), _word("Main", 180, 130), _word("Road", 240, 130),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Zaman Electric Hardware Traders"

    def test_a_far_away_second_line_is_not_merged(self) -> None:
        """The continuation gap is scaled to the winning line's own text
        height — a line much further down the page is a different field,
        not a name wrapping onto a second row."""
        words = [
            _word("Zaman", 50, 50), _word("Traders", 200, 50),
            _word("Invoice", 50, 90),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Zaman Traders"

    def test_a_continuation_line_with_a_digit_is_not_merged(self) -> None:
        """The line directly below a genuine two-line name is virtually
        always an address or shop number, not a name continuation — a
        digit is the generalizable signal that separates the two."""
        words = [
            _word("Zaman", 50, 84), _word("Traders", 200, 84),
            _word("Shop", 50, 100), _word("#", 110, 100), _word("14,", 130, 100), _word("Main", 180, 100), _word("Road", 240, 100),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Zaman Traders"

    def test_a_digit_free_comma_bearing_continuation_line_is_not_merged(self) -> None:
        """Regression guard for a real bug found live: a digit-free address
        fragment ("Hotel view square, Bahria Spring") passed the digit
        check and was wrongly merged onto an unrelated name above it. A
        comma is the generalizable signal an address-list convention uses
        that a business-name suffix essentially never does."""
        words = [
            _word("Riverside", 50, 68), _word("Traders", 200, 68),
            _word("Hotel", 50, 88), _word("view", 110, 88), _word("square,", 160, 88), _word("Central", 240, 88), _word("Town", 320, 88),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders"

    def test_a_moderately_confident_garbled_fragment_is_not_merged(self) -> None:
        """Regression guard for a real bug found live: merging is a
        compounding decision (it trusts two OCR reads together, not one),
        so it holds itself to a stricter confidence floor than accepting a
        single candidate — a two-word garbled OCR fragment (logo/watermark
        noise) cleared the base floor by a hair, passed every other check
        (digit-free, comma-free, close enough), and was wrongly merged onto
        an unrelated name above it."""
        words = [
            _word("Riverside", 50, 68, confidence=0.97), _word("Traders", 200, 68, confidence=0.97),
            _word("wn", 50, 88, confidence=0.86), _word("Rahria", 90, 88, confidence=0.86),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders"


class TestVendorFallbackPrefersHighestConfidenceOverFirstLine:
    """When nothing in the candidate window clears the confidence floor,
    the highest-confidence candidate that still isn't recognisable noise is
    preferred over the plain first-line guess — content over position once
    quality is already compromised."""

    def test_the_higher_confidence_candidate_wins_when_both_are_below_the_floor(self) -> None:
        words = [
            _word("Stamp-9", 50, 50, confidence=0.6),
            _word("Riverside", 50, 100, confidence=0.83), _word("Bakery", 150, 100, confidence=0.83),
            _word("Total: 500", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Bakery"

    def test_still_falls_back_to_the_first_line_when_every_candidate_is_rejected_noise(self) -> None:
        """Regression guard: the new confidence-ranked tier must not swallow
        the original "never return nothing" guarantee — when every
        candidate is reference-shaped or decorative, the plain first line
        is still returned."""
        words = [
            _word("Powered", 50, 50, confidence=0.9), _word("By", 100, 50, confidence=0.9), _word("SomePOS", 130, 50, confidence=0.9),
            _word("B0-9", 50, 100, confidence=0.5),
            _word("Total: 500", 50, 1000, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Powered By SomePOS"


class TestVendorFallbackStripsALeadingReferenceStamp:
    """General-pattern regression guards from a vendor-extraction golden-
    dataset audit (docs/invoice-ocr-plan.md §18): a stamped reference/
    amount marker sometimes sits on the same visual row as the real vendor
    name, immediately ahead of it — found live on three independent real
    receipts. Every example below uses a synthetic vendor unrelated to the
    real dataset — this guards the structural rule (a digit-dominant
    leading word), not any one document or business."""

    def test_a_leading_amount_stamp_is_stripped(self) -> None:
        words = [
            _word("Rs1100", 50, 50), _word("Riverside", 130, 50), _word("Traders", 230, 50),
            _word("Total: 1100", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders"

    def test_a_garbled_amount_stamp_mixing_letters_and_digits_is_still_stripped(self) -> None:
        """The stamp itself need not be cleanly all-digit — "R5540" (an
        OCR misread of "Rs540", the "s" read as another digit-like glyph)
        is still digit-*dominant* once its own letters are counted against
        its digits, which is all this check requires."""
        words = [
            _word("R5540", 50, 50), _word("Riverside", 130, 50), _word("Traders", 230, 50),
            _word("Total: 540", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders"

    def test_a_business_name_that_merely_starts_with_a_digit_is_not_stripped(self) -> None:
        """The digit-*dominant* threshold (over half the alphanumeric
        characters) is what distinguishes a stamp from a real name that
        happens to start with a numeral — "7-Eleven" is mostly letters,
        not mostly digits, and must survive unchanged."""
        words = [_word("7-Eleven", 50, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "7-Eleven"

    def test_a_lone_digit_dominant_word_is_not_stripped_into_nothing(self) -> None:
        """Never reduces a candidate to an empty string — a business name
        genuinely alone on its line, even if (implausibly) digit-shaped,
        is returned as-is rather than discarded."""
        words = [_word("12345", 50, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "12345"


class TestVendorFallbackTruncatesAtARegistrationNumber:
    """A business's own NTN (National Tax Number) sometimes prints on the
    same line as its name rather than on its own line — found live on a
    real receipt. Truncating at it is safe for any vendor: a real business
    name never legitimately contains the literal substring "NTN" followed
    by a registration number."""

    def test_an_ntn_number_glued_onto_the_vendor_line_is_truncated(self) -> None:
        words = [
            _word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Branch-7", 230, 50),
            _word("NTN#0819531-5", 320, 50),
            _word("Total: 500", 50, 1000),
        ]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders Branch-7"

    def test_a_vendor_name_with_no_registration_number_is_unaffected(self) -> None:
        words = [_word("Riverside", 50, 50), _word("Traders", 150, 50), _word("Total: 500", 50, 1000)]
        lines = group_into_lines(words)
        found = find_vendor_name(lines)
        assert found is not None
        assert found[0] == "Riverside Traders"


class TestFindTotalAnywhere:
    """Rule 3.4's fallback treatment applied to `total`, deliberately more
    conservative than vendor/date's own fallbacks — see
    fields.find_total_anywhere's own docstring for why."""

    def test_a_single_unambiguous_amount_with_no_total_label_is_found(self) -> None:
        """Found live: a self-issued cash-receipt voucher states its
        amount in prose ("received amount Rs. 6,000/- as Salary/
        Stipend") with no "Total:" label anywhere on the document."""
        words = [
            _word("received", 50, 100, confidence=0.9), _word("amount", 110, 100, confidence=0.9),
            _word("Rs.", 180, 100, confidence=0.9), _word("6,000/-", 220, 100, confidence=0.9),
            _word("as", 290, 100, confidence=0.9), _word("Salary.", 320, 100, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_total_anywhere(lines)
        assert found is not None
        text, page, bbox = found
        assert text == "6,000/-"

    def test_two_or_more_candidates_is_ambiguous_and_returns_nothing(self) -> None:
        """Two money-shaped amounts with no label to disambiguate which is
        the total — per the conservative "no lost information over a
        wrong guess" principle, this must not guess the larger one."""
        words = [
            _word("Subtotal", 50, 100, confidence=0.9), _word("850.00", 150, 100, confidence=0.9),
            _word("Total", 50, 140, confidence=0.9), _word("920.00", 150, 140, confidence=0.9),
        ]
        # No "total"/"subtotal" label matching happens here — find_total_anywhere
        # itself is purely positional/pattern-based and ignores labels entirely.
        lines = group_into_lines(words)
        assert find_total_anywhere(lines) is None

    def test_a_bare_id_number_is_not_mistaken_for_an_amount(self) -> None:
        """A CNIC/NTN-shaped digit run has no comma, no decimal point, and
        no currency prefix — found live, this would otherwise outrank a
        real total by raw size alone."""
        words = [_word("CNICNo.", 50, 100, confidence=0.9), _word("37405-7883110-8", 150, 100, confidence=0.9)]
        lines = group_into_lines(words)
        assert find_total_anywhere(lines) is None

    def test_no_candidates_at_all_is_none(self) -> None:
        words = [_word("Thank", 50, 100, confidence=0.9), _word("you", 100, 100, confidence=0.9)]
        lines = group_into_lines(words)
        assert find_total_anywhere(lines) is None

    def test_a_low_confidence_candidate_is_skipped(self) -> None:
        words = [_word("6,000.00", 50, 100, confidence=0.4)]
        lines = group_into_lines(words)
        assert find_total_anywhere(lines) is None

    def test_an_amount_ocr_merged_into_the_next_word_is_refused_not_truncated(self) -> None:
        """Regression guard for the worst failure this fallback can have,
        found live during a false-positive audit: a handwritten "Rs. 500"
        on a real cash-receipt voucher OCR'd as the single token
        "Rs.5_cleaning" — the amount merged with the next printed word and
        truncated. Matching its "Rs.5" prefix reported a confident total of
        5.0 for a receipt actually worth 500, *and* moved the document from
        high-priority review down to ordinary review — a wrong number that
        looks more trustworthy than no number. NOT_FOUND is the only safe
        answer when digits run into letters."""
        words = [
            _word("received", 50, 100, confidence=0.94), _word("amount", 130, 100, confidence=0.94),
            _word("Rs.5_cleaning", 200, 100, confidence=0.94),
        ]
        lines = group_into_lines(words)
        assert find_total_anywhere(lines) is None

    def test_a_clean_currency_prefixed_amount_is_still_accepted(self) -> None:
        """The guard above must not also reject the well-formed shape it
        sits next to — "Rs.500" with nothing merged onto it is exactly what
        this fallback exists to find."""
        words = [
            _word("received", 50, 100, confidence=0.94), _word("amount", 130, 100, confidence=0.94),
            _word("Rs.500", 200, 100, confidence=0.94),
        ]
        lines = group_into_lines(words)
        found = find_total_anywhere(lines)
        assert found is not None
        assert found[0] == "Rs.500"

    def test_an_addresss_own_punctuation_commas_are_not_mistaken_for_amounts(self) -> None:
        """Regression guard for a real bug found live on a real cash-receipt
        voucher: a street address ("Plaza 229, Third floor... Phase 7,")
        has plain punctuation commas with nothing but the next word after
        them — these used to count as two extra "amount" candidates
        alongside the real one ("6,000/-"), making a genuinely unambiguous
        total look ambiguous (3 candidates) and refuse to resolve at all."""
        words = [
            _word("Plaza", 50, 50, confidence=0.9), _word("229,", 110, 50, confidence=0.9),
            _word("Third", 170, 50, confidence=0.9), _word("floor,", 230, 50, confidence=0.9),
            _word("Phase", 50, 90, confidence=0.9), _word("7,", 110, 90, confidence=0.9),
            _word("Islamabad", 170, 90, confidence=0.9),
            _word("received", 50, 150, confidence=0.9), _word("amount", 130, 150, confidence=0.9),
            _word("Rs.", 200, 150, confidence=0.9), _word("6,000/-", 240, 150, confidence=0.9),
        ]
        lines = group_into_lines(words)
        found = find_total_anywhere(lines)
        assert found is not None
        assert found[0] == "6,000/-"
