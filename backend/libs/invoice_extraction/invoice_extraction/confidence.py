"""Composite confidence scoring — Section 6. Replaces a single self-reported
model number with a score built from independently-checkable signals, per
docs/invoice-ocr-plan.md §3.

Thresholds below (METHOD_TIERS' values, ROUTING_THRESHOLDS) are reasonable
starting defaults, stated here as exactly that — not numbers derived from
measuring this engine's real accuracy yet, since that requires running it
against a volume of real invoices this project doesn't have collected. See
docs/invoice-ocr-plan.md §6's "exact confidence-combination formula" open
question. Expect to tune these once real auto-process/needs-review outcomes
can be compared against what a human reviewer actually corrects.
"""
from typing import Literal, Optional

# Rule 6.3 — which rule matched a field, tiered by how trustworthy that
# extraction path is. label_anchor+pattern (found via a label AND passed
# value-pattern validation) is the strongest; positional_fallback (e.g.
# "the most prominent top-of-page text" for vendor name with no label
# found) is the weakest that still counts as "found" at all.
METHOD_TIERS: dict[str, float] = {
    "label_anchor+pattern": 1.0,
    "label_anchor": 0.7,
    "positional_fallback": 0.5,
    "not_found": 0.0,
}

# Rule 6.1 — critical accounting fields weigh more than a nice-to-have one
# in the document-level composite; unlisted fields default to 1.0 in
# document_confidence below.
FIELD_WEIGHTS: dict[str, float] = {
    "total": 3.0,
    "tax_amount": 2.0,
    "vendor_name": 2.0,
    "invoice_number": 2.0,
    "subtotal": 1.5,
    "invoice_date": 1.0,
    "ntn": 1.0,
}

CRITICAL_FIELDS: tuple[str, ...] = ("total", "vendor_name", "invoice_number")


def field_confidence(ocr_confidence: float, method: str) -> float:
    """Rule 6.1/6.2: averages the source's OCR confidence with how strong
    the extraction method was, rather than multiplying — a field found via
    the strongest method but from a noisy OCR read (or vice versa) should
    not be crushed to near-zero by one weak input alone."""
    method_tier = METHOD_TIERS.get(method, 0.0)
    return (ocr_confidence + method_tier) / 2


def document_confidence(
    field_confidences: dict[str, float], arithmetic_ok: Optional[bool],
) -> float:
    """Rule 6.1: weighted mean of field confidences, then discounted (not
    zeroed) if the arithmetic checks (Section 5) failed — an arithmetic
    mismatch is a strong signal, not proof every field is wrong."""
    if not field_confidences:
        return 0.0
    weighted_sum = sum(FIELD_WEIGHTS.get(f, 1.0) * c for f, c in field_confidences.items())
    weight_total = sum(FIELD_WEIGHTS.get(f, 1.0) for f in field_confidences)
    base = weighted_sum / weight_total
    if arithmetic_ok is False:
        return base * 0.7
    return base


# Rule 6.4's threshold routing. Deliberately conservative on the low end —
# missing a critical field or a badly-failed arithmetic check routes to the
# high-priority queue and (per Rule 6.4) the caller should consider not
# auto-creating the Invoice record at all until a human confirms.
_AUTO_PROCESS_THRESHOLD = 0.75
_NEEDS_REVIEW_THRESHOLD = 0.4


def route(
    score: float, critical_fields_present: bool, arithmetic_ok: Optional[bool],
) -> Literal["auto_processed", "needs_review", "needs_review_high_priority"]:
    if not critical_fields_present or score < _NEEDS_REVIEW_THRESHOLD:
        return "needs_review_high_priority"
    if score < _AUTO_PROCESS_THRESHOLD or arithmetic_ok is False:
        return "needs_review"
    return "auto_processed"


# Below this per-field confidence, a found value is reported as UNCERTAIN
# rather than FOUND — a reviewer should double-check it, but it's not the
# same signal as the field being entirely absent (NOT_FOUND).
_UNCERTAIN_BELOW = 0.5


def field_status(value_present: bool, confidence: float) -> Literal["FOUND", "NOT_FOUND", "UNCERTAIN"]:
    """The explicit tri-state the review UI needs (docs/invoice-ocr-plan.md's
    receipt-robustness notes): a field is either genuinely absent, present
    but shaky enough to flag, or confidently present — never collapsed into
    a single "here's a value, trust it" signal the way a bare `.value` alone
    would read."""
    if not value_present:
        return "NOT_FOUND"
    if confidence < _UNCERTAIN_BELOW:
        return "UNCERTAIN"
    return "FOUND"
