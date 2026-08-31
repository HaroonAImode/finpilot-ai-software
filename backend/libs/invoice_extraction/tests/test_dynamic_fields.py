"""Generic label/value discovery — the mechanism that surfaces fields this
library has no canonical name for. The layouts here are the real shapes
that drove each rule (see dynamic_fields.py's own module docstring), not
invented ones.
"""
from ocr import PositionedWord

from invoice_extraction.dynamic_fields import discover_fields
from invoice_extraction.lines import group_into_lines


def _word(text: str, x0: float, y0: float) -> PositionedWord:
    return PositionedWord(text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y0 + 12.0, page=0, confidence=1.0)


def _found(words: list[PositionedWord], **kwargs) -> dict[str, str]:
    return {f.key: f.value for f in discover_fields(group_into_lines(words), 0.9, **kwargs)}


class TestSameRowLabelValue:
    def test_an_all_caps_label_and_its_value_are_discovered(self) -> None:
        words = [_word("SHIPPING", 50, 100), _word("HANDLING", 104, 100), _word("$10.00", 400, 100)]
        assert _found(words) == {"shipping_handling": "$10.00"}

    def test_a_colon_terminated_label_is_discovered(self) -> None:
        """Not every real label is upper case — a trailing colon is the
        other signal invoices actually use."""
        words = [_word("Phone:", 50, 100), _word("0300-1234567", 200, 100)]
        assert _found(words) == {"phone": "0300-1234567"}

    def test_the_printed_label_is_preserved_alongside_the_key(self) -> None:
        words = [_word("SHIPPING", 50, 100), _word("&", 102, 100), _word("HANDLING", 112, 100), _word("$10.00", 400, 100)]
        fields = discover_fields(group_into_lines(words), 0.9)
        assert fields[0].label == "SHIPPING & HANDLING"
        assert fields[0].key == "shipping_handling"

    def test_a_discovered_field_carries_its_value_location(self) -> None:
        """The bounding box must point at the *value*, not the label — it's
        what a review UI highlights when the user inspects the field."""
        words = [_word("TAX", 50, 100), _word("$7.67", 400, 100)]
        field = discover_fields(group_into_lines(words), 0.9)[0]
        assert field.bbox is not None
        assert field.bbox[0] >= 400
        assert field.page == 0


class TestHeadingsAreNotFields:
    def test_two_section_headings_side_by_side_are_not_a_label_value_pair(self) -> None:
        """Regression guard for a real layout: "BILLED TO" and "PAYMENT &
        SHIPPING" are two column headings on one visual row. Treating the
        first as a label would make the second its value."""
        words = [
            _word("BILLED", 80, 100), _word("TO", 160, 100),
            _word("PAYMENT", 610, 100), _word("SHIPPING", 700, 100),
        ]
        assert _found(words) == {}

    def test_a_title_alone_on_its_row_does_not_claim_the_row_below(self) -> None:
        """Real case: the document title "SALES INVOICE" sits alone above
        the vendor block, and reading the next row as its value emitted a
        field whose value was the vendor's name."""
        words = [
            _word("SALES", 50, 100), _word("INVOICE", 110, 100),
            _word("LuxuryStore", 50, 140),
        ]
        assert _found(words) == {}

    def test_title_case_text_is_not_treated_as_a_label(self) -> None:
        """A person's name beside their address must not become a field —
        only ALL CAPS or colon-terminated cells are labels."""
        words = [
            _word("Veronica", 80, 100), _word("Costello", 150, 100),
            _word("United", 400, 100), _word("States", 460, 100),
        ]
        assert _found(words) == {}


class TestValueOnTheNextRow:
    def test_a_label_sharing_its_row_reads_the_aligned_cell_below(self) -> None:
        """"PAYMENT METHOD" over "Check / Money order" — the value wraps to
        the next row in the same column."""
        words = [
            _word("Calder,", 50, 100), _word("Michigan", 100, 100),
            _word("PAYMENT", 610, 100), _word("METHOD", 664, 100),
            _word("United", 50, 140), _word("States", 110, 140),
            _word("Check", 610, 140), _word("/", 646, 140), _word("Money", 656, 140), _word("order", 692, 140),
        ]
        assert _found(words) == {"payment_method": "Check / Money order"}


class TestSkipRange:
    def test_the_line_item_table_body_can_be_excluded(self) -> None:
        """Line-item rows are reconstructed properly elsewhere; re-emitting
        their cells here would produce spurious one-off fields."""
        words = [
            _word("ITEMS", 50, 100), _word("QTY", 300, 100), _word("SUBTOTAL", 500, 100),
            _word("WIDGET", 50, 140), _word("1", 300, 140), _word("$43.00", 500, 140),
            _word("TAX", 300, 180), _word("$7.67", 500, 180),
        ]
        lines = group_into_lines(words)
        without_skip = {f.key for f in discover_fields(lines, 0.9)}
        with_skip = {f.key for f in discover_fields(lines, 0.9, range(0, 2))}

        assert "widget" in without_skip
        assert "widget" not in with_skip
        assert "tax" in with_skip


class TestDuplicateLabels:
    def test_the_first_occurrence_of_a_repeated_label_wins(self) -> None:
        words = [
            _word("ORDER", 50, 100), _word("#1001", 300, 100),
            _word("ORDER", 50, 140), _word("#9999", 300, 140),
        ]
        assert _found(words) == {"order": "#1001"}


class TestValuesAreNotMistakenForLabels:
    def test_a_reference_code_is_not_treated_as_a_label(self) -> None:
        """Regression guard for a real case: "PO-55231" contains only the
        letters P and O, so it passed the ALL-CAPS label test and was
        emitted as a label whose value was the *next* field's value. A cell
        containing a digit is a value, never a label."""
        words = [
            _word("PO", 50, 100), _word("#", 64, 100), _word("PO-55231", 300, 100),
            _word("TERMS", 50, 140), _word("Net", 300, 140), _word("30", 322, 140),
        ]
        found = _found(words)
        assert found == {"po": "PO-55231", "terms": "Net 30"}
        assert "po_55231" not in found
