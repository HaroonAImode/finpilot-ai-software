"""Deduplication and vendor-matching helpers — Section 7. Directly relevant
to the accounting use case: duplicate invoice submission (the same paper
invoice re-photographed, or received via both email and WhatsApp) is a real,
common problem this is meant to catch before it creates a second record.
"""
import hashlib
import re
from difflib import get_close_matches
from typing import Optional

_CORPORATE_SUFFIXES = (
    "pvt ltd", "private limited", "(pvt) ltd", "pvt. ltd.", "ltd.", "ltd", "llc", "inc.", "inc",
)


def file_hash(content: bytes) -> str:
    """Rule 7.1 — cheapest, exact-match duplicate check. Compute before
    spending any extraction effort on a file whose hash already exists."""
    return hashlib.sha256(content).hexdigest()


def normalize_vendor_name(name: str) -> str:
    """Rule 7.3: case-folding, whitespace collapse, and common corporate
    suffix stripping — applied before any fuzzy matching, whether that
    matching is against a known vendor list (find_matching_vendor below) or
    another invoice's vendor name (duplicate_key)."""
    normalized = re.sub(r"\s+", " ", name.strip().lower())
    for suffix in _CORPORATE_SUFFIXES:
        pattern = rf"[\s,]*{re.escape(suffix)}$"
        stripped = re.sub(pattern, "", normalized)
        if stripped != normalized:
            normalized = stripped.strip()
            break
    return normalized


def find_matching_vendor(extracted_name: str, known_vendors: list[str], cutoff: float = 0.75) -> Optional[str]:
    """Rule 3.4/7.3: fuzzy-matches a freshly extracted vendor name against
    a small list of already-known vendors, returning the known vendor's
    original (non-normalized) name for display/linking. Uses the standard
    library's difflib rather than adding a fuzzy-matching dependency —
    good enough for matching against a company's own, typically small,
    vendor list; not intended as a general-purpose string-similarity tool.
    """
    normalized_extracted = normalize_vendor_name(extracted_name)
    normalized_to_original = {normalize_vendor_name(v): v for v in known_vendors}
    matches = get_close_matches(
        normalized_extracted, list(normalized_to_original.keys()), n=1, cutoff=cutoff,
    )
    return normalized_to_original[matches[0]] if matches else None


def duplicate_key(vendor_name: str, invoice_number: str, total: float, invoice_date_iso: str) -> tuple:
    """Rule 7.2: (vendor, invoice number, total, date) match, even with a
    different file hash, is a likely duplicate worth flagging for review —
    catches the re-scan/re-photograph/received-twice cases Rule 7.1's exact
    file-hash check cannot."""
    return (
        normalize_vendor_name(vendor_name),
        invoice_number.strip().lower(),
        round(total, 2),
        invoice_date_iso,
    )
