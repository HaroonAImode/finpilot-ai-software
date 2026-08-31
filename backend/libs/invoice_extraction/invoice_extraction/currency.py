"""Currency detection — never defaults to a currency when no evidence
exists on the document. This is a direct, deliberate reaction to a real bug
documented in a separate reference project's own implementation report: an
unconditional "else default USD" fallback in its currency detector. This
module only ever reports a currency it actually found text evidence for.
"""
import re

from invoice_extraction.schema import FieldValue, not_found

# Checked in this order (first match wins). Word/abbreviation entries use
# lookaround rather than \b so "Rs." (period immediately after, no trailing
# word character) still matches — \b alone fails there, since there's no
# word/non-word transition between "." and a following space.
#
# Bare single-character symbols ($, €, £, ₨) are required to sit next to a
# digit, and are still weaker evidence than a word/code match even then —
# confirmed necessary against real garbled OCR from a photographed receipt,
# where a stray misrecognized company-logo glyph produced a lone "$9" with
# no real currency mention anywhere on the document. A multi-letter code or
# word (PKR, Rupees, USD, ...) matching by OCR-noise coincidence is far less
# likely than a single misrecognized character — so those get full
# confidence, while a bare-symbol match is reported at just above the
# UNCERTAIN threshold (confidence.field_status) rather than as a confident
# fact, honestly reflecting that a single glyph can't fully rule out noise.
_WORD_PATTERNS: list[tuple[str, str]] = [
    (r"\bpkr\b", "PKR"),
    (r"(?<![A-Za-z])rs\.?(?![A-Za-z])", "PKR"),
    (r"\brupees?\b", "PKR"),
    (r"\beuros?\b", "EUR"),
    (r"\beur\b", "EUR"),
    (r"\bgbp\b", "GBP"),
    (r"\bpounds?\b", "GBP"),
    (r"us\$", "USD"),
    (r"\busd\b", "USD"),
    (r"\bus\s*dollars?\b", "USD"),
]

_SYMBOL_PATTERNS: list[tuple[str, str]] = [
    (r"₨\s?\d", "PKR"),
    (r"€\s?\d", "EUR"),
    (r"£\s?\d", "GBP"),
    (r"\$\s?\d", "USD"),
]

_COMPILED_WORDS = [(re.compile(p, re.IGNORECASE), code) for p, code in _WORD_PATTERNS]
_COMPILED_SYMBOLS = [(re.compile(p), code) for p, code in _SYMBOL_PATTERNS]

# Deliberately below confidence.field_status's UNCERTAIN threshold (0.5) —
# a bare-symbol match should surface as UNCERTAIN in the review UI, not
# FOUND, given the confirmed real risk of a single misrecognized glyph
# (see the Samsuddin Siddiqui test fixture: a garbled company-logo mark
# OCR'd as "$9" with no real currency mention anywhere on that document).
_SYMBOL_MATCH_CONFIDENCE = 0.45


def detect_currency(text: str) -> FieldValue:
    """Scans the document's raw OCR text for the first currency notation
    found — word/code evidence checked first (strongest, confidence 1.0),
    falling back to a bare symbol adjacent to a digit (weaker, still above
    the UNCERTAIN floor but not asserted as confidently as a real word
    match). No default, no guess when nothing matches either way."""
    for pattern, code in _COMPILED_WORDS:
        if pattern.search(text):
            return FieldValue(value=code, confidence=1.0, method="pattern_match")
    for pattern, code in _COMPILED_SYMBOLS:
        if pattern.search(text):
            return FieldValue(value=code, confidence=_SYMBOL_MATCH_CONFIDENCE, method="pattern_match_weak")
    return not_found()
