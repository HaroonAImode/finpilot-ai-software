"""Line-item table reconstruction — Section 4, the hardest part and the
primary source of accuracy loss per every source in the research (see
docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md §0/§4).

**Scope, stated honestly**: this implements Rule 4.2 (gap-based column
detection), 4.3 (header-row keyword anchoring), 4.4's wrapped-description
merge heuristic, and 4.5 (totals-boundary detection). It does **not**
implement Rule 4.1 (ruling-line/border geometric detection) — that requires
detecting drawn lines on the rendered page image (Hough transform or
vector-graphics parsing of the PDF content stream), a materially larger,
separate piece of work from word-position clustering. Documented here as a
known gap and a candidate future enhancement, not silently skipped.
"""
import re
from dataclasses import dataclass, field

from ocr import PositionedWord

from invoice_extraction.labels import LINE_ITEM_HEADER_KEYWORDS, TOTALS_SECTION_KEYWORDS
from invoice_extraction.lines import line_text

# A word is assigned to a column if its left edge falls at or past the
# column's start, minus a small tolerance for a value that starts slightly
# left of its header (common with right-aligned numeric columns whose
# header is left-aligned).
_COLUMN_TOLERANCE = 5.0

#: A token that is *only* digits/commas/a decimal point — no currency
#: symbol, no letters. Deliberately stricter than validators.parse_money's
#: pattern (which `.search()`es for a number anywhere in a longer string):
#: this decides whether a whole word is a bare number, to tell a genuine
#: description ("Widget", "SKU-1234") apart from a rate/amount/qty value
#: that band-containment alone put in the wrong column.
_BARE_NUMBER = re.compile(r"^-?[\d,]+(?:\.\d{1,2})?$")

#: The mirror case — a token with at least one letter and no digit at all.
#: "SKU-1234" or "5pcs" are neither this nor `_BARE_NUMBER`, and are left
#: to band containment: they are genuinely ambiguous, unlike plain prose.
_ALPHABETIC = re.compile(r"^[^\d]*[A-Za-z][^\d]*$")


@dataclass
class RawLineItemRow:
    description: str = ""
    qty_text: str = ""
    rate_text: str = ""
    amount_text: str = ""
    review_flags: list[str] = field(default_factory=list)


def find_header_row(lines: list[list[PositionedWord]]) -> int | None:
    """Rule 4.3: a single keyword match is unreliable (a stray "Total" in a
    payment-terms sentence, say) — 2+ column-header keywords in the same
    row is the actual signal."""
    for i, line in enumerate(lines):
        text = line_text(line).lower()
        matched_fields = {
            field_name
            for field_name, keywords in LINE_ITEM_HEADER_KEYWORDS.items()
            if any(kw in text for kw in keywords)
        }
        if len(matched_fields) >= 2:
            return i
    return None


def find_totals_boundary(
    lines: list[list[PositionedWord]], start: int,
    columns: dict[str, tuple[float, float]] | None = None,
) -> int:
    """Rule 4.5: the first row at/after `start` naming a totals-section
    keyword ends the line-item region.

    When `columns` is supplied, keywords appearing inside the *description*
    column are ignored. A totals row puts its label outside the description
    column — right-aligned toward the amounts ("SUBTOTAL   $93.00") — while
    a line item whose own description happens to contain a totals word is
    still a line item.

    Found live, and it emptied an entire table: a real Pakistani invoice's
    "PAYMENT SUMMARY" table has "Total Booking Amount" as the description of
    its *first data row*. Matching on the whole row put the boundary
    immediately after the header, so every row of that table was discarded
    and the document reported no line items at all.

    Without `columns` this keeps its original whole-row behaviour, so
    callers that have not detected columns yet are unaffected.
    """
    description_band = columns.get("description") if columns else None

    for i in range(start, len(lines)):
        if description_band is None:
            candidate_text = line_text(lines[i]).lower()
        else:
            d_x0, d_x1 = description_band
            outside = [w for w in lines[i] if not (d_x0 <= w.x0 < d_x1)]
            candidate_text = " ".join(w.text for w in outside).lower()
        if any(kw in candidate_text for kw in TOTALS_SECTION_KEYWORDS):
            return i
    return len(lines)


#: A trailing parenthetical, stripped before matching — "Amount(Rs)" is
#: just as likely to arrive as one OCR-glued token, no space, as it is two.
#: Only the trailing form: this must not touch a bigram check's own
#: internal space ("qty. particulars"), or it would cut the string down to
#: its first word and silently lose the second one entirely.
_TRAILING_PAREN = re.compile(r"\([^)]*\)\s*$")


def _match_header_keyword(text: str) -> str | None:
    # Real headers are routinely abbreviated with a trailing period
    # ("Qty.") or run straight into a parenthetical with no space
    # ("Amount(Rs)") — an exact match against "qty"/"amount" misses both.
    # Found live, both on the same real receipt: "Qty." went undetected,
    # dropping its item's name into the gap where no column claimed it;
    # "Amount(Rs)" went undetected too, so nothing distinguished the rate
    # value from the amount value and both numbers landed in "rate"
    # together. `.rstrip` only ever removes trailing characters — it must
    # not be reached for on the whole *bigram* string, or "qty. particulars"
    # would lose "particulars" the same way a naive prefix-match would.
    text = _TRAILING_PAREN.sub("", text).rstrip(".:")
    for field_name, keywords in LINE_ITEM_HEADER_KEYWORDS.items():
        if text in keywords:
            return field_name
    return None


def detect_columns(header_line: list[PositionedWord], page_right_edge: float) -> dict[str, tuple[float, float]]:
    """Rule 4.2 step 4: anchor columns to the header row's own x-positions
    rather than re-deriving them per row. Checks adjacent-word bigrams
    before single words, since real headers are often two words ("Unit
    Price", "Hourly rate" — both seen in this library's real test
    documents) that a single-word-only match would miss.
    """
    sorted_words = sorted(header_line, key=lambda w: w.x0)
    matches: list[tuple[str, float]] = []
    used: set[int] = set()

    for i in range(len(sorted_words)):
        if i in used:
            continue
        if i + 1 < len(sorted_words) and i + 1 not in used:
            bigram = f"{sorted_words[i].text.lower()} {sorted_words[i + 1].text.lower()}"
            field_name = _match_header_keyword(bigram)
            if field_name:
                matches.append((field_name, sorted_words[i].x0))
                used.add(i)
                used.add(i + 1)
                continue
        field_name = _match_header_keyword(sorted_words[i].text.lower())
        if field_name:
            matches.append((field_name, sorted_words[i].x0))
            used.add(i)

    matches.sort(key=lambda m: m[1])
    columns: dict[str, tuple[float, float]] = {}
    for idx, (field_name, x0) in enumerate(matches):
        x1 = matches[idx + 1][1] if idx + 1 < len(matches) else page_right_edge
        columns[field_name] = (x0 - _COLUMN_TOLERANCE, x1)
    return columns


def reconstruct_rows(
    lines: list[list[PositionedWord]], columns: dict[str, tuple[float, float]],
) -> list[RawLineItemRow]:
    """Assigns each word on each row to whichever column band its left edge
    falls in, then applies Rule 4.4's two cheapest, most reliable failure
    detections:

    - a row with description text but no numeric content in any of
      qty/rate/amount is very likely a wrapped continuation of a line
      item's description, not a new line item — merged into whichever
      numeric row is visually *nearest* (by y-distance) rather than always
      the previous one. Confirmed live this direction matters: a real
      invoice can put the item name *above* its qty/price row and a SKU
      sub-line *below* it (name / qty+price / SKU, three visual rows per
      item) — a backward-only merge glued the SKU line onto the right
      row but then kept gluing every following description-only line
      (including the *next* item's name) onto that same row too, since
      nothing ever started a new one. Nearest-by-y-distance naturally
      resolves both directions using the same real spacing an invoice
      already uses to separate one item's own lines from the next item's.
    - a row using noticeably fewer columns than the header defines gets
      flagged (`sparse_row_possible_merged_cell`) rather than silently
      force-fit into the standard shape.
    - a bare number (no letters — see `_BARE_NUMBER`) is never accepted
      into the description column, even when its left edge falls inside
      that column's band. Found live on a real handwritten receipt: the
      rate value's bounding box drifted left of its own header by more
      than `_COLUMN_TOLERANCE`, which put it inside the (much wider)
      description band instead — the receipt's price ended up glued onto
      the item name as text, and both rate and total came back empty. A
      bare number is routed to whichever *numeric* column's header is
      nearest by x-position instead: on messy real documents (handwritten,
      photographed) a value's own box drifts from its header more freely
      than the header positions drift from each other, so "nearest header"
      is a better estimator here than a fixed-width band. Free-text words
      (a real description, an alphanumeric SKU) are untouched by this and
      still resolved by band containment as before.
    - the mirror image: a word containing a letter and no digit — plainly
      prose, never a rate/qty/amount value — is never accepted into a
      *numeric* column, even when band containment says it belongs there.
      Same receipt, same cause: the item name's own box started well left
      of the "Particulars" header, inside what was, by band width alone,
      still the "Qty." column's territory. Real invoices essentially never
      put free text in a quantity/rate/amount cell, so it is moved to
      description instead, when one is defined.
    """
    numeric_fields = [f for f in columns if f != "description"]

    entries = []
    for line in lines:
        if not line:
            continue
        cells: dict[str, list[str]] = {field_name: [] for field_name in columns}
        for word in sorted(line, key=lambda w: w.x0):
            if numeric_fields and _BARE_NUMBER.match(word.text):
                nearest = min(
                    numeric_fields,
                    key=lambda f: abs(word.x0 - (columns[f][0] + _COLUMN_TOLERANCE)),
                )
                cells[nearest].append(word.text)
                continue
            contained = next(
                (f for f, (x0, x1) in columns.items() if x0 <= word.x0 < x1), None,
            )
            if contained in numeric_fields and "description" in columns and _ALPHABETIC.match(word.text):
                contained = "description"
            if contained is not None:
                cells[contained].append(word.text)
        entries.append({
            "y0": line[0].y0,
            "description": " ".join(cells.get("description", [])),
            "qty_text": " ".join(cells.get("qty", [])),
            "rate_text": " ".join(cells.get("rate", [])),
            "amount_text": " ".join(cells.get("amount", [])),
            "has_numeric": any(cells.get(f) for f in ("qty", "rate", "amount")),
        })

    numeric_indices = [i for i, e in enumerate(entries) if e["has_numeric"]]

    if not numeric_indices:
        # No row in this region has any numeric content at all — nothing to
        # anchor a nearest-row merge to. Falls back to the simple
        # merge-everything-into-one-row behavior rather than emitting one
        # spurious row per description-only line.
        rows: list[RawLineItemRow] = []
        for e in entries:
            if not e["description"]:
                continue
            if rows:
                rows[0].description = f"{rows[0].description} {e['description']}".strip()
            else:
                rows.append(RawLineItemRow(description=e["description"]))
        if rows and columns and 1 < len(columns) - 1:
            rows[0].review_flags.append("sparse_row_possible_merged_cell")
        return rows

    row_by_index: dict[int, RawLineItemRow] = {
        i: RawLineItemRow(
            description=entries[i]["description"],
            qty_text=entries[i]["qty_text"],
            rate_text=entries[i]["rate_text"],
            amount_text=entries[i]["amount_text"],
        )
        for i in numeric_indices
    }

    for i, e in enumerate(entries):
        if i in row_by_index or not e["description"]:
            continue
        nearest = min(numeric_indices, key=lambda ni: abs(entries[ni]["y0"] - e["y0"]))
        target = row_by_index[nearest]
        target.description = f"{target.description} {e['description']}".strip()

    rows = []
    for i in numeric_indices:
        row = row_by_index[i]
        field_values = {
            "description": row.description, "qty": row.qty_text,
            "rate": row.rate_text, "amount": row.amount_text,
        }
        filled_columns = sum(1 for f in columns if field_values.get(f))
        if columns and filled_columns < len(columns) - 1:
            row.review_flags.append("sparse_row_possible_merged_cell")
        rows.append(row)
    return rows
