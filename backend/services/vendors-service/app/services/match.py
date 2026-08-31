"""Suggests which existing vendor a raw OCR `vendor_name` probably is.

Rules-based string similarity (stdlib `difflib`), not an LLM or embedding
model — consistent with the OCR pipeline's own extraction rules (see
docs/invoice-ocr-plan.md): deterministic, explainable, and free of a model
dependency for a service this small.

A suggestion is never applied automatically. "ABC Traders" and "ABD Traders"
score high on pure string similarity and could be two different real
suppliers — the cost of a wrong auto-link (spend silently misattributed to
the wrong vendor) is worse than the cost of one extra click, so a human
always makes the actual decision. This module only ranks candidates.
"""
from difflib import SequenceMatcher

from app.models import Vendor
from app.schemas.reconciliation import VendorMatchSuggestion

#: Below this, two names are more likely coincidence than the same vendor
#: misspelled — showing a suggestion here would train the accountant to
#: stop reading them before clicking, exactly the failure mode this whole
#: feature exists to avoid on the OCR side.
MIN_SCORE = 0.55

#: Enough to catch a genuine misread without turning every row into a wall
#: of low-confidence guesses to compare.
MAX_SUGGESTIONS = 3


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def suggest_matches(raw_name: str, vendors: list[Vendor]) -> list[VendorMatchSuggestion]:
    normalized_raw = _normalize(raw_name)
    scored = [
        (SequenceMatcher(None, normalized_raw, _normalize(vendor.name)).ratio(), vendor)
        for vendor in vendors
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        VendorMatchSuggestion(vendor_id=vendor.id, name=vendor.name, score=round(score, 3))
        for score, vendor in scored[:MAX_SUGGESTIONS]
        if score >= MIN_SCORE
    ]
