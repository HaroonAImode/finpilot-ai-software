"""Section 4 — line-item table reconstruction. Synthetic word layouts here
cover multi-row tables, wrapped descriptions, and sparse rows that the real
sample invoices (single line item each) don't exercise — see
test_extract_invoice_real_documents.py for the real-document coverage.
"""
from ocr import PositionedWord

from invoice_extraction.line_items import detect_columns, find_header_row, find_totals_boundary, reconstruct_rows
from invoice_extraction.lines import group_into_lines

PAGE_WIDTH = 600.0


def _word(text: str, x0: float, y0: float, page: int = 0) -> PositionedWord:
    return PositionedWord(text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=page, confidence=1.0)


def _table_words() -> list[PositionedWord]:
    """A four-column table: Description | Qty | Rate | Amount, three rows,
    one with a wrapped description continuing onto its own visual line."""
    return [
        # header row
        _word("Description", 50, 100), _word("Qty", 250, 100), _word("Rate", 320, 100), _word("Amount", 420, 100),
        # row 1
        _word("Steel", 50, 120), _word("Sheet", 90, 120), _word("12", 250, 120), _word("9800", 320, 120), _word("117600", 420, 120),
        # row 2: description wraps onto its own line with no numeric values
        _word("Welding", 50, 140), _word("Rods", 100, 140), _word("6", 250, 140), _word("3200", 320, 140), _word("19200", 420, 140),
        _word("(box", 50, 160), _word("of", 90, 160), _word("50)", 110, 160),
        # row 3
        _word("Transport", 50, 180), _word("Charges", 130, 180), _word("1", 250, 180), _word("8500", 320, 180), _word("8500", 420, 180),
        # totals section
        _word("Subtotal", 50, 200), _word("155300", 420, 200),
        _word("Tax", 50, 220), _word("27954", 420, 220),
        _word("Total", 50, 240), _word("183254", 420, 240),
    ]


class TestFindHeaderRow:
    def test_a_row_with_two_or_more_keywords_is_the_header(self) -> None:
        lines = group_into_lines(_table_words())
        idx = find_header_row(lines)
        assert idx is not None
        assert "Description" in [w.text for w in lines[idx]]

    def test_a_single_stray_keyword_is_not_mistaken_for_a_header(self) -> None:
        """A lone 'Total' in a payment-terms sentence must not trigger —
        Rule 4.3 requires 2+ keywords together."""
        words = [_word("Payment", 50, 100), _word("Total", 130, 100), _word("due", 190, 100), _word("in", 230, 100), _word("30", 260, 100), _word("days", 290, 100)]
        lines = group_into_lines(words)
        assert find_header_row(lines) is None


class TestFindTotalsBoundary:
    def test_the_boundary_lands_on_the_first_totals_keyword_row(self) -> None:
        lines = group_into_lines(_table_words())
        header_idx = find_header_row(lines)
        boundary = find_totals_boundary(lines, header_idx + 1)
        assert "Subtotal" in [w.text for w in lines[boundary]]


class TestDetectColumns:
    def test_all_four_columns_are_anchored_to_the_header_row(self) -> None:
        lines = group_into_lines(_table_words())
        header_idx = find_header_row(lines)
        columns = detect_columns(lines[header_idx], PAGE_WIDTH)
        assert set(columns) == {"description", "qty", "rate", "amount"}
        # columns should be in left-to-right order matching the header
        ordered = sorted(columns.items(), key=lambda kv: kv[1][0])
        assert [name for name, _ in ordered] == ["description", "qty", "rate", "amount"]

    def test_a_header_abbreviated_with_a_trailing_period_is_recognized(self) -> None:
        """Regression guard for a real handwritten receipt: its header read
        "Qty." (with the period), which matched nothing in
        LINE_ITEM_HEADER_KEYWORDS's exact "qty" — so no qty column was ever
        detected, and the item's own name (sitting in the resulting gap)
        was silently dropped rather than reaching any column at all."""
        header = [_word("Qty.", 50, 100), _word("Particulars", 250, 100), _word("Rate", 420, 100), _word("Amount", 500, 100)]
        columns = detect_columns(header, PAGE_WIDTH)
        assert set(columns) == {"qty", "description", "rate", "amount"}

    def test_a_bigram_with_a_period_on_its_first_word_is_not_mistaken_for_the_whole_bigram(self) -> None:
        """The fix above must not go too far: stripping "Qty." down to "qty"
        must never happen *inside* a two-word bigram check, or "Qty.
        Particulars" would collapse to matching "qty" alone and swallow
        "Particulars" into that match — losing the description column
        exactly the way the period bug did, just via a different path."""
        header = [_word("Qty.", 50, 100), _word("Particulars", 150, 100)]
        columns = detect_columns(header, PAGE_WIDTH)
        assert set(columns) == {"qty", "description"}

    def test_a_header_glued_to_a_parenthetical_with_no_space_is_recognized(self) -> None:
        """Regression guard for the same real receipt: its amount column
        header was OCR'd as one token, "Amount(Rs)", no space — which also
        matched nothing exactly. With no amount column detected, the rate
        and amount values had nothing to tell them apart and both numbers
        landed in "rate" together."""
        header = [_word("Qty.", 50, 100), _word("Particulars", 250, 100), _word("Rate", 420, 100), _word("Amount(Rs)", 500, 100)]
        columns = detect_columns(header, PAGE_WIDTH)
        assert set(columns) == {"qty", "description", "rate", "amount"}

    def test_items_and_subtotal_headers_are_recognized(self) -> None:
        """Regression guard for a real bug found live on a real e-commerce
        order-confirmation invoice: its table header was literally "ITEMS
        | QTY | SUBTOTAL" — neither the plural "ITEMS" nor "SUBTOTAL"
        matched any LINE_ITEM_HEADER_KEYWORDS entry, so only the "qty"
        column was ever detected. With just one column recognized, its
        band silently swallowed every other column's words (spanning all
        the way to the page's right edge), scrambling quantities and
        dollar amounts together."""
        header = [_word("ITEMS", 50, 100), _word("QTY", 250, 100), _word("SUBTOTAL", 400, 100)]
        columns = detect_columns(header, PAGE_WIDTH)
        assert set(columns) == {"description", "qty", "amount"}


class TestReconstructRows:
    def test_three_line_items_are_reconstructed_with_correct_cell_values(self) -> None:
        lines = group_into_lines(_table_words())
        header_idx = find_header_row(lines)
        totals_idx = find_totals_boundary(lines, header_idx + 1)
        columns = detect_columns(lines[header_idx], PAGE_WIDTH)

        rows = reconstruct_rows(lines[header_idx + 1:totals_idx], columns)

        assert len(rows) == 3
        assert rows[0].qty_text == "12"
        assert rows[0].rate_text == "9800"
        assert rows[0].amount_text == "117600"

    def test_a_wrapped_description_line_is_merged_into_the_row_above_not_kept_separate(self) -> None:
        """Rule 4.4: a 'row' with only description text and no numeric
        values is almost certainly a continuation of the previous item's
        description, not a new line item."""
        lines = group_into_lines(_table_words())
        header_idx = find_header_row(lines)
        totals_idx = find_totals_boundary(lines, header_idx + 1)
        columns = detect_columns(lines[header_idx], PAGE_WIDTH)

        rows = reconstruct_rows(lines[header_idx + 1:totals_idx], columns)

        assert rows[1].description == "Welding Rods (box of 50)"
        assert rows[1].amount_text == "19200"

    def test_a_sparse_row_is_flagged_not_silently_force_fit(self) -> None:
        """Rule 4.4: far fewer filled columns than the header defines is a
        review-flag signal (e.g. a merged discount row), not something to
        pretend fits the standard shape."""
        columns = {"description": (50, 250), "qty": (250, 320), "rate": (320, 420), "amount": (420, 600)}
        sparse_row = [_word("Discount", 50, 300), _word("applied", 110, 300)]
        lines = [sparse_row]

        rows = reconstruct_rows(lines, columns)

        assert rows[0].review_flags == ["sparse_row_possible_merged_cell"]

    def test_item_name_before_and_sku_line_after_the_numeric_row_both_merge_into_it(self) -> None:
        """Regression guard for a real bug found live on a real e-commerce
        order-confirmation invoice: some layouts print the item name
        *above* its qty/price row and a SKU sub-line *below* it (three
        visual rows per item: name / qty+amount / SKU) rather than the
        simpler name-and-numbers-on-one-line layout _table_words() covers.
        A backward-only merge glued the SKU line onto the right row but
        then kept gluing every following description-only line — including
        the *next* item's own name — onto that same row, since nothing
        ever started a new one. Nearest-by-y-distance merging must resolve
        both directions using the same real vertical spacing that already
        separates one item's lines from the next item's."""
        columns = {"description": (50, 250), "qty": (250, 320), "amount": (320, 600)}
        words = [
            _word("Luma", 50, 100), _word("Watch", 100, 100),
            _word("1", 251, 106), _word("43.00", 320, 106),
            _word("SKU:", 50, 112), _word("24-WG09", 90, 112),
            _word("Fusion", 50, 140), _word("Backpack", 100, 140),
            _word("1", 251, 146), _word("50.00", 320, 146),
            _word("SKU:", 50, 152), _word("24-MB02", 90, 152),
        ]
        lines = group_into_lines(words)

        rows = reconstruct_rows(lines, columns)

        assert len(rows) == 2
        assert rows[0].description == "Luma Watch SKU: 24-WG09"
        assert rows[0].amount_text == "43.00"
        assert rows[1].description == "Fusion Backpack SKU: 24-MB02"
        assert rows[1].amount_text == "50.00"


class TestColumnDriftOnMessyRealDocuments:
    """A value's own bounding box can drift from its header's by more than
    `_COLUMN_TOLERANCE` on a handwritten or photographed document, without
    the header positions themselves moving — band containment alone then
    misassigns it to whichever (often much wider) column happens to still
    contain that drifted position. Regression guard for a real receipt:
    "YZ Paint & Hardware", where exactly this happened to both a rate value
    and the item name in the same row."""

    def test_a_price_that_drifts_into_the_description_band_still_reaches_rate(self) -> None:
        """The real failure: "350" (rate) sat at x0=584, left of its own
        "Rate" column's tolerance-adjusted start (593) but still squarely
        inside the wide description band (319-598) — so it was accepted
        into description instead, and rate/amount both came back empty."""
        columns = {"description": (319.0, 598.0), "rate": (593.0, 672.0), "amount": (667.0, 866.0)}
        words = [
            _word("Hathori", 209, 100), _word("Small", 358, 100),
            PositionedWord(text="350", x0=584.0, y0=100, x1=684.0, y1=112, page=0, confidence=1.0),
            PositionedWord(text="350", x0=708.0, y0=100, x1=810.0, y1=112, page=0, confidence=1.0),
        ]

        rows = reconstruct_rows([words], columns)

        assert rows[0].rate_text == "350"
        assert rows[0].amount_text == "350"
        assert "350" not in rows[0].description

    def test_an_item_name_that_drifts_into_a_numeric_column_still_reaches_description(self) -> None:
        """The mirror failure: the item name started at x0=209, inside what
        was still the "Qty." column's territory (107-324) because that
        column had nothing to its left except page margin — so "Hathori"
        landed in the qty cell as garbage text instead of joining the
        description it was actually part of."""
        columns = {"qty": (107.0, 324.0), "description": (319.0, 598.0), "rate": (593.0, 672.0), "amount": (667.0, 866.0)}
        words = [
            _word("Hathori", 209, 100), _word("Small", 358, 100),
            PositionedWord(text="350", x0=584.0, y0=100, x1=684.0, y1=112, page=0, confidence=1.0),
            PositionedWord(text="350", x0=708.0, y0=100, x1=810.0, y1=112, page=0, confidence=1.0),
        ]

        rows = reconstruct_rows([words], columns)

        assert rows[0].description == "Hathori Small"
        assert rows[0].qty_text == ""

    def test_a_normally_aligned_row_is_unaffected_by_either_heuristic(self) -> None:
        """The existing, correctly-aligned case must keep working exactly
        as before — these heuristics only apply when a word's own position
        disagrees with band containment, not when it agrees."""
        lines = group_into_lines(_table_words())
        header_idx = find_header_row(lines)
        totals_idx = find_totals_boundary(lines, header_idx + 1)
        columns = detect_columns(lines[header_idx], PAGE_WIDTH)

        rows = reconstruct_rows(lines[header_idx + 1:totals_idx], columns)

        assert rows[0].description == "Steel Sheet"
        assert rows[0].qty_text == "12"
        assert rows[0].rate_text == "9800"
        assert rows[0].amount_text == "117600"

    def test_a_mixed_alphanumeric_token_is_left_to_band_containment(self) -> None:
        """"24-WG09" is neither a bare number nor plain prose — genuinely
        ambiguous, unlike a real quantity or a real description word — so
        neither new heuristic should redirect it. Placed inside the qty
        band on purpose: if the free-text heuristic wrongly matched it,
        this would move to description instead of staying put."""
        columns = {"description": (50, 250), "qty": (250, 320), "amount": (320, 600)}
        words = [_word("24-WG09", 260, 112), _word("43.00", 320, 112)]

        rows = reconstruct_rows([words], columns)

        assert rows[0].qty_text == "24-WG09"


class TestTotalsBoundaryRespectsColumns:
    def test_a_line_item_described_as_a_total_does_not_end_the_table(self) -> None:
        """Regression guard for a real Pakistani invoice that lost its
        entire table. Its "PAYMENT SUMMARY" section lists "Total Booking
        Amount" as the description of the *first data row* — matching
        totals keywords across the whole row put the boundary immediately
        after the header, so every row was discarded and the document
        reported no line items at all. A totals keyword inside the
        description column belongs to that item's description."""
        columns = {"description": (35, 420), "amount": (415, 560)}
        words = [
            _word("Total", 40, 130), _word("Booking", 75, 130), _word("Amount", 125, 130), _word("4,000", 470, 130),
            _word("Advance", 40, 160), _word("Paid", 90, 160), _word("-1,000", 465, 160),
        ]
        lines = group_into_lines(words)

        assert find_totals_boundary(lines, 0, columns) == len(lines)
        # Without column awareness the old whole-row match still applies.
        assert find_totals_boundary(lines, 0) == 0

    def test_a_real_totals_row_outside_the_description_column_still_ends_it(self) -> None:
        """The genuine case must keep working: a totals label sits right of
        the description column, near the amounts."""
        columns = {"description": (74, 502), "qty": (502, 823), "amount": (823, 1000)}
        words = [
            _word("Widget", 79, 100), _word("1", 505, 100), _word("$43.00", 844, 100),
            _word("SUBTOTAL", 539, 140), _word("$93.00", 843, 140),
        ]
        lines = group_into_lines(words)

        assert find_totals_boundary(lines, 0, columns) == 1
