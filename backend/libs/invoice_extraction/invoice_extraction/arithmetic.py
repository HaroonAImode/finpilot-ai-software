"""Arithmetic / cross-field validation — Section 5. The single best
confidence signal available, and it costs nothing extra to compute: every
check here is pure arithmetic over values already extracted, not a new
extraction pass.
"""
from datetime import date, timedelta
from typing import Literal, Optional

# Rounding tolerance for "does this arithmetic check out" — accounts for
# normal cent/paisa rounding conventions, not floating-point noise alone.
_TOLERANCE = 0.02

# Pakistan's standard sales tax/GST rates in production use. Not an
# exhaustive legal reference — a pragmatic set for Rule 5.3's plausibility
# check, extend if a real invoice surfaces a valid rate not covered here.
KNOWN_PK_TAX_RATES = (0.0, 0.05, 0.08, 0.10, 0.13, 0.15, 0.16, 0.17, 0.18)

# P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md P0-2/
# P0-3): how far `total` may plausibly sit from a document-internal
# reference amount before it reads as implausible rather than trusted
# outright. Deliberately a RATIO against the document's OWN other numbers,
# never an absolute ceiling — a real, hand-audited case this project
# already has proves why: a Rs. 990,000 invoice whose own subtotal+tax
# already sum to Rs. 990,000 is exactly as plausible as a Rs. 2,750 one; a
# fixed global maximum would reject the former as "too large" for no
# financial reason at all, which is precisely the "dumb global maximum"
# this check is designed not to be. 0.2–5.0 is generous enough to absorb a
# real discount, a rounding difference, or a service charge (none of which
# plausibly move a total more than 5x away from subtotal+tax-discount in
# either direction) while still catching the actual failure shape observed
# live: a receipt with subtotal ≈ Rs. 2,770 whose `total` field resolved to
# Rs. 277,000 — a ratio of ~100, nowhere near this band.
_MIN_PLAUSIBLE_MAGNITUDE_RATIO = 0.2
_MAX_PLAUSIBLE_MAGNITUDE_RATIO = 5.0


def _within_tolerance(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= _TOLERANCE * max(abs(actual), 1.0)


def check_line_item(
    qty: Optional[float], rate: Optional[float], amount: Optional[float],
) -> Literal["pass", "fail", "not_checked"]:
    """Rule 5.1: qty × rate ≈ amount. A mismatch is a strong signal of
    column misalignment during extraction (Rule 4.4), not just a bad OCR
    read of one digit — flag the row, not just the document."""
    if qty is None or rate is None or amount is None:
        return "not_checked"
    return "pass" if _within_tolerance(qty * rate, amount) else "fail"


def check_totals(
    subtotal: Optional[float], tax: Optional[float], discount: Optional[float], total: Optional[float],
) -> Optional[bool]:
    """Rule 5.2: subtotal + tax − discount ≈ total. None (not False) when
    there isn't enough to check — that is a different, weaker signal than a
    genuine mismatch and callers should not conflate the two."""
    if subtotal is None or total is None:
        return None
    expected = subtotal + (tax or 0.0) - (discount or 0.0)
    return _within_tolerance(expected, total)


def is_plausible_tax_rate(subtotal: Optional[float], tax_amount: Optional[float]) -> Optional[bool]:
    """Rule 5.3: an implied tax rate outside the known set of valid
    Pakistani rates is a signal that either the tax or subtotal field was
    misread, not that the vendor charges an unusual rate."""
    if not subtotal or tax_amount is None:
        return None
    implied = tax_amount / subtotal
    return any(abs(implied - rate) <= 0.01 for rate in KNOWN_PK_TAX_RATES)


def is_plausible_invoice_date(
    invoice_date: Optional[date], today: date, max_age_days: int = 730,
) -> Optional[bool]:
    """Rule 5.4: catches OCR digit-confusion (0/8, 1/7, 3/8 are documented
    common Tesseract confusions) that a pure format-regex check would miss
    — a date that parses cleanly but lands in the future or decades in the
    past is still wrong."""
    if invoice_date is None:
        return None
    if invoice_date > today:
        return False
    if invoice_date < today - timedelta(days=max_age_days):
        return False
    return True


def total_reference_amount(
    subtotal: Optional[float], tax: Optional[float], discount: Optional[float],
    line_items_amount_sum: Optional[float],
) -> Optional[float]:
    """P0-B: the best available document-internal amount `total` ought to
    roughly agree with. This is never a guess at what the *correct* total
    is — only a reference to compare a candidate against.

    Prefers a subtotal when one was extracted at all; falls back to the
    line-item sum only when no subtotal exists. Either way, tax/discount
    are applied on top the same way check_totals above already validates
    them (subtotal *or* line-item sum + tax - discount) — a document whose
    subtotal wasn't found but whose line items and tax both were should
    still get an accurate reference, not one silently missing the tax
    component. A document with neither a subtotal nor any line items has no
    internal reference left, and returns None — the same "nothing to check
    against" convention check_totals/is_plausible_tax_rate already use,
    never a silent assumption that an unreferenced total is fine.
    """
    base = subtotal if subtotal is not None else line_items_amount_sum
    if base is None:
        return None
    return base + (tax or 0.0) - (discount or 0.0)


def is_plausible_total_magnitude(total: Optional[float], reference: Optional[float]) -> Optional[bool]:
    """P0-B: is `total` the right order of magnitude relative to a
    document-internal reference amount (see total_reference_amount above)?

    None when there is no reference to check against at all — callers must
    treat this as "unverified," never as "verified." A ratio outside
    [_MIN_PLAUSIBLE_MAGNITUDE_RATIO, _MAX_PLAUSIBLE_MAGNITUDE_RATIO] is the
    deterministic signal that a candidate is suspicious — not proof it is
    wrong (a genuinely unusual invoice can still fall outside a reference
    built from an incomplete subtotal/line-item read), which is exactly why
    callers route this to REVIEW, never a silent rejection.
    """
    if total is None or reference is None or reference <= 0:
        return None
    ratio = total / reference
    return _MIN_PLAUSIBLE_MAGNITUDE_RATIO <= ratio <= _MAX_PLAUSIBLE_MAGNITUDE_RATIO


def shares_source_location(
    a_page: Optional[int], a_bbox: Optional[tuple[float, float, float, float]],
    b_page: Optional[int], b_bbox: Optional[tuple[float, float, float, float]],
) -> bool:
    """P0-B: true when two fields were extracted from the exact same
    page+bbox — a structural signal, independent of what either value
    actually is, that a label-anchor collision happened rather than a
    genuine document layout. A real document always prints two
    conceptually different fields (e.g. subtotal and total) as two separate
    lines, even when their values legitimately coincide (no tax charged, so
    subtotal == total) — never from the identical source coordinates.

    Found live investigating the exact real bug this project's own P0 audit
    is named for: on a real receipt, `subtotal` and `total` both resolved
    to the identical bbox — two different label matchers had anchored onto
    the same OCR-garbled text region, so both fields carried the same wrong
    number and passed check_totals by agreeing with each other, which is
    precisely why a value-only arithmetic check cannot catch this shape of
    error and a source-location check is needed alongside it.
    """
    if a_page is None or b_page is None or a_bbox is None or b_bbox is None:
        return False
    return a_page == b_page and a_bbox == b_bbox
