"""Groups PositionedWords back into visual lines/rows — the shared
prerequisite for both label-anchored field extraction (fields.py) and
line-item table reconstruction (line_items.py). One page at a time; callers
loop over `ExtractionResult.words_by_page` themselves so a multi-page
document's line numbering never gets confused across page boundaries.
"""
from ocr import PositionedWord

#: Two boxes belong to the same visual row when they overlap vertically by at
#: least this fraction of the *shorter* box's height.
#:
#: Vertical overlap, not y0-proximity — a real bug found live once PaddleOCR
#: replaced Tesseract as the primary engine. Tesseract emits uniform
#: word-level boxes whose y0 values line up almost exactly across a row, so a
#: tight y0 tolerance worked; PaddleOCR emits *region*-level boxes whose
#: height and baseline vary with the text they wrap. On a real invoice its
#: boxes for one visually-single row read
#:   "Dec 14, 2020, 4:18:34" y 315-342,  "ORDER DATE" y 327-348,  "PM" y 336-358
#: — y0 values 21px apart, so a y0-proximity check split one row into three
#: and the label ("ORDER DATE") ended up on a different "line" from its own
#: value, which is exactly why label-anchored date extraction silently found
#: nothing. Overlap ratio is the metric that survives both shapes.
#:
#: 0.6 rather than 0.5: measured against this project's own real PaddleOCR
#: output, 0.5 also swept up boxes that genuinely belong to the *next* row
#: (a wrapped SKU sub-line clipping the following item's band), while 0.6
#: still merges every true same-row pair seen. Not a guessed constant.
_MIN_VERTICAL_OVERLAP = 0.6

#: Fallback for degenerate zero-height boxes (no height to compute a ratio
#: from) — the original y0 tolerance, preserved so such boxes group exactly
#: as they always did rather than each becoming its own line.
_DEGENERATE_Y_TOLERANCE = 4.0


def _vertical_overlap_ratio(a_y0: float, a_y1: float, b_y0: float, b_y1: float) -> float:
    overlap = min(a_y1, b_y1) - max(a_y0, b_y0)
    if overlap <= 0:
        return 0.0
    shorter = min(a_y1 - a_y0, b_y1 - b_y0)
    return overlap / shorter if shorter > 0 else 0.0


def group_into_lines(words: list[PositionedWord]) -> list[list[PositionedWord]]:
    """Groups words into visual rows by vertical overlap against the row's
    running band (the union of the boxes already placed in it).

    The band is a union, so a row can absorb boxes that are individually
    taller or lower than the one that started it — necessary for real OCR
    output where a row's tallest box (a currency amount, say) sets the true
    row extent. To stop that union from chaining down a whole column, a
    candidate must *also* have its own vertical centre inside the band:
    clipping the band's edge is not enough to join a row, the box has to sit
    within it.
    """
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w.y0, w.x0))
    lines: list[list[PositionedWord]] = [[sorted_words[0]]]
    bands: list[tuple[float, float]] = [(sorted_words[0].y0, sorted_words[0].y1)]

    for word in sorted_words[1:]:
        band_y0, band_y1 = bands[-1]
        if band_y1 <= band_y0 or word.y1 <= word.y0:
            same_row = abs(word.y0 - band_y0) <= _DEGENERATE_Y_TOLERANCE
        else:
            centre = (word.y0 + word.y1) / 2
            same_row = (
                _vertical_overlap_ratio(band_y0, band_y1, word.y0, word.y1) >= _MIN_VERTICAL_OVERLAP
                and band_y0 <= centre <= band_y1
            )

        if same_row:
            lines[-1].append(word)
            bands[-1] = (min(band_y0, word.y0), max(band_y1, word.y1))
        else:
            lines.append([word])
            bands.append((word.y0, word.y1))

    for line in lines:
        line.sort(key=lambda w: w.x0)
    return lines


def line_text(line: list[PositionedWord]) -> str:
    return " ".join(w.text for w in line)


def line_bbox(line: list[PositionedWord]) -> tuple[float, float, float, float]:
    return (
        min(w.x0 for w in line), min(w.y0 for w in line),
        max(w.x1 for w in line), max(w.y1 for w in line),
    )
