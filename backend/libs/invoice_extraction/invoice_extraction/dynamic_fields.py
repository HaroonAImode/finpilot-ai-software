"""Generic label/value discovery — surfaces *every* labelled field on a
document, not only the ones this library has a canonical name for.

Why this exists: the canonical extractors (fields.py) each hunt for one
known field by its known label variants, so anything a particular vendor
prints that isn't on that list — "Shipping & Handling", "Order Number",
"Payment Method", "PO #" — is read by OCR, sits right there in the words,
and is then silently discarded. On a real e-commerce invoice that meant a
$10.00 shipping line existed on the page, was never extracted, and the
totals then failed arithmetic validation by exactly that $10.00.

Deliberately rules-only, no LLM — same standing constraint as the rest of
this pipeline. The rules are deliberately conservative: this runs over
*every* line of the document, so a loose rule produces pages of noise. Two
signals drive it, both chosen because they are what real invoices actually
use to mark a label:

1. the cell is ALL CAPS ("INVOICE NUMBER", "TAX", "SHIPPING & HANDLING"), or
2. the cell ends in a colon ("Phone:", "SKU:").

Title-case text is explicitly *not* treated as a label, which is what keeps
a person's name ("Veronica Costello") sitting next to their address from
being emitted as a field whose value is the address.
"""
import re
from dataclasses import dataclass
from typing import Optional

from ocr import PositionedWord

from invoice_extraction.lines import line_bbox

#: A gap wider than this between adjacent words ends one cell and starts the
#: next. Same reasoning and rough scale as fields.py's own column-gap
#: threshold — comfortably above normal inter-word spacing, comfortably
#: below a real column jump.
_CELL_GAP = 28.0

#: Labels longer than this are almost certainly a sentence that happens to
#: be capitalised (a footer notice, a terms paragraph), not a field label.
_MAX_LABEL_WORDS = 4

#: A value cell must show at least one of these to count as a value rather
#: than another heading: a digit, a lowercase letter, or a currency/symbol
#: character. Pure ALL-CAPS alphabetic text is a heading — real example,
#: found live: "BILLED TO" and "PAYMENT & SHIPPING" sit side by side as two
#: section headings on one visual row, and without this rule the first is
#: emitted as a field whose value is the second.
_VALUE_EVIDENCE = re.compile(r"[0-9a-z$€£₨%]")

#: Stripped from a label before it becomes a key. "#" is included because
#: real labels write it as decoration ("PO #", "Invoice #").
_LABEL_TRIM = " :#-.\t"

#: Stripped from a *value*. Deliberately narrower than _LABEL_TRIM: "#" is
#: part of the value for a reference number printed as "#6000000001", so
#: stripping it would silently alter the extracted data.
_VALUE_TRIM = " :\t"


@dataclass
class DiscoveredField:
    """One label/value pair found anywhere on the document."""

    #: snake_case identifier derived from the printed label.
    key: str
    #: The label exactly as printed, so a UI can show the vendor's own
    #: wording rather than this library's normalisation of it.
    label: str
    value: str
    confidence: float
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None


def _cells(line: list[PositionedWord]) -> list[list[PositionedWord]]:
    """Splits one visual row into cells at column-sized horizontal gaps."""
    if not line:
        return []
    ordered = sorted(line, key=lambda w: w.x0)
    cells: list[list[PositionedWord]] = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.x0 - prev.x1 > _CELL_GAP:
            cells.append([cur])
        else:
            cells[-1].append(cur)
    return cells


def _text(cell: list[PositionedWord]) -> str:
    return " ".join(w.text for w in cell).strip()


def _is_label(cell: list[PositionedWord]) -> bool:
    text = _text(cell)
    if not text or len(cell) > _MAX_LABEL_WORDS:
        return False
    stripped = text.strip(_LABEL_TRIM)
    if not stripped or not any(c.isalpha() for c in stripped):
        return False
    # A cell containing a digit is a value, not a label. Without this, a
    # reference code like "PO-55231" passes the ALL-CAPS test (its only
    # letters are P and O) and becomes a label whose "value" is whatever
    # sits in the next column — seen live, where the value of "PO #" was
    # then itself emitted as a field labelled "PO-55231".
    if any(c.isdigit() for c in stripped):
        return False
    if text.rstrip().endswith(":"):
        return True
    # ALL CAPS — ignoring the punctuation and connectors real labels contain
    # ("SHIPPING & HANDLING", "PO #").
    letters = [c for c in stripped if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _is_value(cell: list[PositionedWord]) -> bool:
    text = _text(cell).strip(_VALUE_TRIM)
    return bool(text) and bool(_VALUE_EVIDENCE.search(text))


def _key_for(label: str) -> str:
    cleaned = label.strip(_LABEL_TRIM).lower()
    cleaned = re.sub(r"[&/]", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    return cleaned.strip("_")


def _value_from_next_line(
    label_cell: list[PositionedWord], next_line: list[PositionedWord],
) -> Optional[list[PositionedWord]]:
    """A label that is the last cell on its row usually has its value on the
    row below, in the same column — "PAYMENT METHOD" over "Check / Money
    order". Only the cell horizontally aligned with the label is considered,
    so a neighbouring column's text on that row is never picked up."""
    label_x0 = label_cell[0].x0
    for cell in _cells(next_line):
        if abs(cell[0].x0 - label_x0) <= _CELL_GAP and _is_value(cell):
            return cell
    return None


def discover_fields(
    lines: list[list[PositionedWord]],
    confidence: float,
    skip_line_range: Optional[range] = None,
) -> list[DiscoveredField]:
    """Walks every row and emits each label/value pair it can justify.

    `skip_line_range` excludes the line-item table's own body: those rows
    are already reconstructed properly as line items, and their cells would
    otherwise be re-emitted here as spurious one-off fields.

    First occurrence of a key wins — invoices repeat labels (a summary
    block echoing a header field), and the first is the one printed in the
    document's own primary position.
    """
    skip = set(skip_line_range) if skip_line_range else set()
    found: dict[str, DiscoveredField] = {}

    for i, line in enumerate(lines):
        if i in skip:
            continue
        cells = _cells(line)
        for position, cell in enumerate(cells):
            if not _is_label(cell):
                continue

            value_cell: Optional[list[PositionedWord]] = None
            if position + 1 < len(cells) and _is_value(cells[position + 1]):
                value_cell = cells[position + 1]
            elif (
                position == len(cells) - 1
                # A label sitting alone on its row is a document title or a
                # section heading, not a field label — reading the row below
                # it as its "value" is how "SALES INVOICE" ended up as a
                # field whose value was the vendor's name (seen live).
                # Sharing a row with other content is what distinguishes a
                # real label whose value wrapped to the next line.
                and len(cells) > 1
                and i + 1 < len(lines)
                and (i + 1) not in skip
            ):
                value_cell = _value_from_next_line(cell, lines[i + 1])

            if value_cell is None:
                continue

            label = _text(cell).strip(_LABEL_TRIM)
            key = _key_for(label)
            if not key or key in found:
                continue

            found[key] = DiscoveredField(
                key=key,
                label=label,
                value=_text(value_cell).strip(_VALUE_TRIM),
                confidence=confidence,
                page=value_cell[0].page,
                bbox=line_bbox(value_cell),
            )

    return list(found.values())
