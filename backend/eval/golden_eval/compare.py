"""Field-level comparison logic — actual pipeline output vs. golden-dataset
ground truth. Pure stdlib, no dependency on the OCR/extraction pipeline
itself, so this is independently unit-testable with synthetic examples
(see tests/test_compare.py) and safe to reuse from any future evaluation
script without dragging the whole pipeline in.

Five possible outcomes per field, per the benchmark spec:
- CORRECT       — actual matches known-correct ground truth
- INCORRECT     — actual has a value, ground truth is known, they disagree
- MISSING       — ground truth is known, actual has no value at all
- UNVERIFIABLE  — ground truth itself could not be reliably established;
                  never counted toward accuracy in either direction
- FALSE_POSITIVE — ground truth is known to be *absent* (e.g. `total` on a
                  confirmed non-transactional document), but actual has a
                  value anyway — the single most dangerous shape of error
                  this benchmark exists to catch, so it is never conflated
                  with an ordinary MISSING or INCORRECT.

The MISSING/INCORRECT distinction is deliberately never collapsed (Rule 5
of the benchmark spec): a wrong number reads as a real, present, plausible
value to anyone who doesn't check it against a receipt — treating it as
"no worse than missing" would hide the one error class most likely to
reach a ledger uncorrected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

#: Ground truth could not be reliably established for this field on this
#: document — OCR too garbled, no independent source available, or the
#: Excel/receipt relationship itself is ambiguous. Excluded from every
#: accuracy calculation; reported separately as its own count.
UNKNOWN = "UNKNOWN"

#: Ground truth *is* known, and the known-correct answer is "this field
#: should carry no value" — e.g. `total` on a document independently
#: confirmed non-transactional. Distinct from UNKNOWN: this is a real,
#: verifiable expectation, and violating it (actual has a value) is a
#: FALSE_POSITIVE, not a benign miss.
ABSENT = "ABSENT"


class FieldStatus(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    MISSING = "MISSING"
    UNVERIFIABLE = "UNVERIFIABLE"
    FALSE_POSITIVE = "FALSE_POSITIVE"


@dataclass(frozen=True)
class FieldResult:
    field: str
    expected: Any
    actual: Any
    status: FieldStatus


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def compare_exact(field: str, expected: Any, actual: Any) -> FieldResult:
    """Case-insensitive, whitespace-normalized exact match — used for
    `document_type` and booleans (`transactional`) where the pipeline's
    vocabulary is a small closed set and any deviation is meaningful, not
    noise to be smoothed over."""
    if expected == UNKNOWN:
        return FieldResult(field, expected, actual, FieldStatus.UNVERIFIABLE)
    if expected == ABSENT:
        if _is_blank(actual):
            return FieldResult(field, expected, actual, FieldStatus.CORRECT)
        return FieldResult(field, expected, actual, FieldStatus.FALSE_POSITIVE)
    if _is_blank(actual):
        return FieldResult(field, expected, actual, FieldStatus.MISSING)
    norm_expected = expected.strip().lower() if isinstance(expected, str) else expected
    norm_actual = actual.strip().lower() if isinstance(actual, str) else actual
    status = FieldStatus.CORRECT if norm_expected == norm_actual else FieldStatus.INCORRECT
    return FieldResult(field, expected, actual, status)


def compare_text(field: str, expected: Any, actual: Any) -> FieldResult:
    """Same rule as compare_exact, split out under its own name for text
    fields (vendor, category) — case-insensitive, whitespace-normalized
    exact string match. Deliberately not fuzzy/substring matching: a
    benchmark exists to measure the pipeline honestly, and "close enough"
    string matching would quietly launder a partial extraction (e.g. the
    real, previously-shipped bug where "Azeem" alone was extracted instead
    of "Azeem Electric & Hardware Store") into a false CORRECT."""
    return compare_exact(field, expected, actual)


#: A cent's worth of float slop — currency values pass through JSON/float
#: round-trips (e.g. "2200.00" -> 2200.0) that must not register as a
#: mismatch, while anything larger is a genuine amount disagreement, never
#: rounding noise.
_AMOUNT_TOLERANCE = 0.005


def compare_amount(field: str, expected: Any, actual: Any) -> FieldResult:
    """Numeric comparison for financial fields (`total`) — never string
    comparison, which would treat "2200.0" and "2,200.00" as different
    values for no financially meaningful reason, and never a wide fuzzy
    tolerance, which would risk exactly the failure mode Rule 5 of the
    benchmark spec warns against (a materially wrong amount waved through
    as "close enough")."""
    if expected == UNKNOWN:
        return FieldResult(field, expected, actual, FieldStatus.UNVERIFIABLE)
    if expected == ABSENT:
        if actual is None:
            return FieldResult(field, expected, actual, FieldStatus.CORRECT)
        return FieldResult(field, expected, actual, FieldStatus.FALSE_POSITIVE)
    if actual is None:
        return FieldResult(field, expected, actual, FieldStatus.MISSING)
    try:
        matches = abs(float(expected) - float(actual)) <= _AMOUNT_TOLERANCE
    except (TypeError, ValueError):
        matches = False
    return FieldResult(field, expected, actual, FieldStatus.CORRECT if matches else FieldStatus.INCORRECT)


#: Formats seen across this pipeline's own `invoice_date` output and the
#: Excel's own date column — extend as new real shapes are found, same
#: "grow from evidence" discipline as the rest of this codebase's parsing.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y")


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def compare_date(field: str, expected: Any, actual: Any) -> FieldResult:
    """Compares calendar dates, not date strings — "2026-06-18" and
    "18 June 2026" are the same date and must not register as a mismatch
    just because the two sources format dates differently. An expected or
    actual value that doesn't parse as a date at all is treated as absent
    for that side (MISSING or INCORRECT, whichever the parse failure
    lands on), never silently ignored."""
    if expected == UNKNOWN:
        return FieldResult(field, expected, actual, FieldStatus.UNVERIFIABLE)
    if expected == ABSENT:
        if _is_blank(actual):
            return FieldResult(field, expected, actual, FieldStatus.CORRECT)
        return FieldResult(field, expected, actual, FieldStatus.FALSE_POSITIVE)
    if _is_blank(actual):
        return FieldResult(field, expected, actual, FieldStatus.MISSING)
    expected_date, actual_date = _parse_date(expected), _parse_date(actual)
    if expected_date is None:
        # Ground truth itself isn't a parseable date — a golden-dataset
        # authoring bug, not a pipeline result to score. Surface it loudly
        # rather than silently treating it as a pass or fail.
        raise ValueError(f"{field}: golden-dataset expected value {expected!r} is not a parseable date")
    status = FieldStatus.CORRECT if actual_date == expected_date else FieldStatus.INCORRECT
    return FieldResult(field, expected, actual, status)
