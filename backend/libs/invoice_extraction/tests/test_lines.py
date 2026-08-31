"""Visual-row grouping (lines.py) — the shared prerequisite for both
label-anchored field extraction and line-item table reconstruction.

The geometry in TestPaddleOcrRegionBoxes below is copied from this project's
own real PaddleOCR output for a real invoice, not invented: the whole reason
this module changed from y0-proximity to vertical-overlap grouping was that
PaddleOCR's region-level boxes don't share the near-identical y0 values
Tesseract's word-level boxes do.
"""
from ocr import PositionedWord

from invoice_extraction.lines import group_into_lines


def _box(text: str, x0: float, y0: float, y1: float) -> PositionedWord:
    return PositionedWord(text=text, x0=x0, y0=y0, x1=x0 + len(text) * 6.0, y1=y1, page=0, confidence=1.0)


def _texts(lines: list[list[PositionedWord]]) -> list[list[str]]:
    return [[w.text for w in line] for line in lines]


class TestUniformWordBoxes:
    """Tesseract-shaped input: uniform heights, near-identical y0 per row."""

    def test_words_sharing_a_baseline_group_into_one_row(self) -> None:
        words = [_box("Total", 50, 100, 112), _box("183254", 420, 100, 112)]
        assert _texts(group_into_lines(words)) == [["Total", "183254"]]

    def test_clearly_separate_rows_stay_separate(self) -> None:
        words = [_box("Subtotal", 50, 100, 112), _box("Tax", 50, 140, 152)]
        assert _texts(group_into_lines(words)) == [["Subtotal"], ["Tax"]]

    def test_each_row_is_ordered_left_to_right(self) -> None:
        words = [_box("Amount", 420, 100, 112), _box("Qty", 250, 100, 112), _box("Description", 50, 100, 112)]
        assert _texts(group_into_lines(words)) == [["Description", "Qty", "Amount"]]


class TestPaddleOcrRegionBoxes:
    def test_a_label_and_its_value_with_staggered_baselines_share_a_row(self) -> None:
        """Regression guard for a real bug found live after the PaddleOCR
        migration. These three boxes are one visually-single row on a real
        invoice ("ORDER DATE  Dec 14, 2020, 4:18:34 PM") but PaddleOCR
        reported y0 values 21px apart, so the previous 4px y0-proximity
        check split them into three separate "lines" — putting the label
        "ORDER DATE" on a different line from its own value, which is
        precisely why label-anchored date extraction returned nothing at
        all for this document."""
        words = [
            _box("Dec 14, 2020, 4:18:34", 751, 315, 342),
            _box("ORDER DATE", 544, 327, 348),
        ]
        assert _texts(group_into_lines(words)) == [["ORDER DATE", "Dec 14, 2020, 4:18:34"]]

    def test_a_totals_row_with_differing_box_heights_groups(self) -> None:
        """Real geometry: the amount's box is taller than its label's."""
        words = [_box("TAX", 600, 985, 1016), _box("$7.67", 855, 984, 1017)]
        assert _texts(group_into_lines(words)) == [["TAX", "$7.67"]]

    def test_the_next_table_row_is_not_absorbed_into_the_previous_one(self) -> None:
        """The anti-chaining guard: a row's band grows as boxes join it, so
        without requiring a candidate's own centre to sit inside that band,
        a growing band could swallow the following row. Real geometry from
        the last line item and the totals row directly beneath it."""
        words = [
            _box("SKU: 24-MB02", 79, 845, 872),
            _box("SUBTOTAL", 539, 889, 920),
            _box("$93.00", 843, 888, 921),
        ]
        assert _texts(group_into_lines(words)) == [["SKU: 24-MB02"], ["SUBTOTAL", "$93.00"]]


class TestDegenerateBoxes:
    def test_zero_height_boxes_still_group_by_y0_proximity(self) -> None:
        """A box with no height has no ratio to compute, so those fall back
        to the original y0 tolerance rather than each becoming its own row."""
        words = [_box("Label", 50, 100, 100), _box("Value", 300, 102, 102)]
        assert _texts(group_into_lines(words)) == [["Label", "Value"]]


class TestEmptyInput:
    def test_no_words_is_no_lines(self) -> None:
        assert group_into_lines([]) == []
