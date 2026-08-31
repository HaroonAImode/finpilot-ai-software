"""Value-pattern validation — Rule 3.3. These are filters, not finders: a
candidate value has already been located near a label (fields.py); these
functions catch the common failure where the label-adjacent text isn't
actually the value (a page number, another label, stray punctuation).
"""
import re
from datetime import date, datetime
from typing import Optional

# Checked in priority order, not all at once — Pakistani SME context favors
# day-first dates, so those are tried before the US month-first format.
# Rule 3.3 notes a more robust version would detect the dominant format
# *across* a document's own date fields rather than a fixed global priority
# order; that refinement is a documented future enhancement (this plan's
# §6-style open questions), not implemented here — a fixed priority order
# already resolves the common ambiguous cases (dd/mm vs mm/dd) correctly
# for any day value over 12, and Pakistani invoices are day-first by
# convention for the rest.
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y",
    # 2-digit-year variants, still day-first — common on real local-shop
    # receipts ("20/8/26"). Tried after every 4-digit-year format so a
    # genuine 4-digit year is never misread as one; %y's own pivot (00-68 ->
    # 2000-2068) correctly resolves "26" to 2026, confirmed empirically.
    "%d/%m/%y", "%d-%m-%y",
    "%m/%d/%Y",
]


#: Strips a trailing clock-time (optionally with AM/PM) off a combined
#: date+time value, e.g. "Dec 14, 2020, 4:18:34 PM" -> "Dec 14, 2020".
#: Found live on a real e-commerce order-confirmation invoice ("ORDER DATE"
#: printed as a full timestamp) — none of _DATE_FORMATS has (or should
#: have) a time component, so without this the whole label-anchored value
#: fails every format and the date is silently lost even though it's
#: right there.
_TRAILING_TIME_PATTERN = re.compile(r",?\s*\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?\.?$")

#: An ordinal-day suffix ("1st", "2nd", "3rd", "4th"..."31st") sometimes
#: OCRs as something other than its own letters — a bare "t" (the "h"
#: dropped), or a stray quotation mark standing in for a misread
#: superscript ("30\" June 2026") — found live on real internal memos that
#: print their own signature date this way. The exact garbled shape isn't
#: itself meaningful, only its position is: a short, closed set of
#: characters sitting between a 1-2 digit day number and a month name.
#: Restricted to the ordinal-suffix letters themselves (s, t, n, d, r, h)
#: plus quote-like characters, deliberately NOT a broad "any character"
#: class — a broad class would risk eating the first letters of the month
#: name itself (e.g. misreading "17Jun" as day "17" + noise "Ju" + month
#: "n"), which this restricted set cannot do since none of "J"/"u" are in
#: it.
_ORDINAL_SUFFIX_NOISE = re.compile(r"^(\d{1,2})[stndrhSTNDRH\"'′″]{1,2}(?=\s|[A-Za-z])")

#: A day/month or month/year boundary sometimes prints (or OCRs) with the
#: separating space missing entirely — "17Jun 2026", "28 Jun2026" — found
#: live on real POS receipts. Re-inserted before the fixed single-space
#: literals in _DATE_FORMATS are tried, rather than duplicating every
#: format string with an optional-whitespace variant.
_MISSING_SEPARATOR_DIGIT_LETTER = re.compile(r"(\d)([A-Za-z])")
_MISSING_SEPARATOR_LETTER_DIGIT = re.compile(r"([A-Za-z])(\d)")


def _normalize_date_text(text: str) -> str:
    """Structural OCR-noise normalization shared by every date-parsing
    entry point below — never a lookup of specific values, only a
    position- and shape-based cleanup applied before any format is tried.
    Order matters: the ordinal-suffix strip must run before the missing-
    separator insertion, since inserting a space between (say) "30" and
    "t" first would move the noise out of reach of the anchored strip."""
    text = _ORDINAL_SUFFIX_NOISE.sub(r"\1", text)
    text = _MISSING_SEPARATOR_DIGIT_LETTER.sub(r"\1 \2", text)
    text = _MISSING_SEPARATOR_LETTER_DIGIT.sub(r"\1 \2", text)
    return text


def parse_date(text: str) -> Optional[date]:
    cleaned = text.strip().strip(".,")
    cleaned = _TRAILING_TIME_PATTERN.sub("", cleaned).strip().strip(".,")
    cleaned = _normalize_date_text(cleaned)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


# Broader than _TRAILING_TIME_PATTERN: that one strips a *well-separated*
# trailing time (a comma or space before the clock digits). Found live on a
# real Pakistani POS receipt, a date can be glued directly onto its
# trailing time with zero separator at all — "18/06/20264:30:13PM" — where
# there is no boundary character to anchor a strip on. These patterns
# instead search for a date-shaped run of characters anywhere inside the
# text and let the fixed-width `\d{4}` year group naturally stop before
# the glued-on time digits, rather than trying to strip the time first.
# Checked in the same day-first-preferred order as _DATE_FORMATS.
_DATE_SUBSTRING_PATTERNS = [
    re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}"),
    # Whitespace around the month name is optional, not mandatory — an
    # ordinal-suffix noise character (see _ORDINAL_SUFFIX_NOISE) or a
    # missing separator (see _MISSING_SEPARATOR_*) can sit where a plain
    # space normally would on a day-first textual date. This only widens
    # what counts as a *candidate substring*; parse_date's own normalize-
    # then-strptime pass still has to successfully parse it as a real
    # month name afterwards, so a coincidental digit-letter-digit run that
    # isn't actually a date is rejected exactly as before.
    re.compile(r"\d{1,2}(?:[stndrhSTNDRH\"'′″]{1,2})?\s*[A-Za-z]{3,9}[,.]?\s*\d{4}"),
    re.compile(r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"),
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
    re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2}(?!\d)"),
]


def find_date_in_text(text: str) -> Optional[date]:
    """Broader than parse_date: that one requires the *entire* cleaned
    string to match a known format exactly, which is right for a clean
    label-anchored value but too strict for text a date is merely
    embedded in. Tries the strict whole-string parse first (so every
    existing parse_date behaviour/caller is unchanged), then falls back to
    finding the first date-shaped substring anywhere in the text and
    parsing just that — recovers a date glued to other text with no
    separator at all (see _DATE_SUBSTRING_PATTERNS) and is also what
    fields.find_date_anywhere uses to test a positional candidate line
    with no date label at all."""
    parsed = parse_date(text)
    if parsed is not None:
        return parsed
    for pattern in _DATE_SUBSTRING_PATTERNS:
        match = pattern.search(text)
        if match:
            parsed = parse_date(match.group(0))
            if parsed is not None:
                return parsed
    return None


#: A slash/dash numeric date whose first two components could each
#: plausibly be either the day or the month ("06/02/2026") — the exact
#: shape _DATE_FORMATS' day-first-preferred try-order (§17's own documented
#: convention) has always silently resolved one way, with nothing ever
#: surfacing that the resolution was a guess rather than a certainty. Only
#: the numeric slash/dash shape is genuinely ambiguous this way: a textual
#: month name ("10 June 2026") can never be misread as the day, and an ISO
#: date's leading 4-digit year makes its own field order unambiguous — this
#: pattern's `\d{1,2}` first group structurally cannot match either of
#: those, so it only ever fires on the one shape that actually needs it.
#:
#: Two alternatives, tried 4-digit-year first, mirroring
#: _DATE_SUBSTRING_PATTERNS' own ordering exactly — and for the same reason
#: that module's own comment gives: a genuine 4-digit year must be checked
#: first so a 2-digit-year match never truncates it. The 4-digit branch
#: deliberately has no trailing boundary requirement at all (found live: a
#: date glued directly onto a trailing time with zero separator,
#: "06/02/202613.05:15" — a trailing `\b` or `(?!\d)` after the year would
#: fail to match here, since more digits immediately follow; the fixed
#: `\d{4}` width is what already lets find_date_in_text recover this same
#: shape). The 2-digit branch keeps `(?!\d)` since without a fixed width
#: there, it would otherwise be indistinguishable from the leading two
#: digits of an unglued 4-digit year.
_AMBIGUOUS_NUMERIC_DATE_PATTERN = re.compile(
    r"\b(?P<a>\d{1,2})[/-](?P<b>\d{1,2})[/-]\d{4}|\b(?P<a2>\d{1,2})[/-](?P<b2>\d{1,2})[/-]\d{2}(?!\d)"
)


def is_day_month_ambiguous(text: str) -> bool:
    """P1-C: true when `text` contains a numeric date whose day/month order
    cannot be determined without an assumption — found live: a real
    document's own "06/02/2026" is genuinely ambiguous (both "6 Feb" and
    "2 June" are valid interpretations), and cross-checking a *different*
    document from the same source proved that source's own convention is
    month-first, contradicting this pipeline's Pakistani-context day-first
    default (docs/invoice-ocr-plan.md §17). Resolving that specific case
    would require knowing which vendor issued it — exactly the hardcoding
    this project forbids — so this is deliberately never used to pick a
    different parse. It exists only as evidence: letting a clearer,
    unambiguous candidate elsewhere in the same document outscore this one,
    and discounting confidence when this is all there is, per §17's own
    "left unresolved rather than guessed" precedent, now actually reflected
    in the field's own reported confidence instead of only in documentation.
    """
    match = _AMBIGUOUS_NUMERIC_DATE_PATTERN.search(text)
    if not match:
        return False
    a = int(match.group("a") or match.group("a2"))
    b = int(match.group("b") or match.group("b2"))
    return 1 <= a <= 12 and 1 <= b <= 12


# Currency prefix/suffix, thousands separators, optional decimals — covers
# "1,234.56", "1234.56", "Rs. 1,234", "PKR 1,234.00", "$1.00 USD".
# The leading "-" is captured, not skipped: a real invoice writes credits
# that way ("Advance Paid  -1,000" on a Pakistani payment-summary table,
# seen live) and dropping the sign silently turns a deduction into a
# charge — the kind of error that reconciles to exactly twice the amount.
_MONEY_PATTERN = re.compile(
    r"(?:rs\.?|pkr|\$|usd|sgd|gbp|eur)?\s*(-?[\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|pkr|usd|sgd|gbp|eur)?",
    re.IGNORECASE,
)

#: Accounting-style negatives, written as "(1,000)" rather than "-1,000".
_PARENTHESISED_NEGATIVE = re.compile(r"\(\s*(?:rs\.?|pkr|\$|usd|sgd|gbp|eur)?\s*[\d,]+(?:\.\d{1,2})?\s*\)", re.IGNORECASE)


def parse_money(text: str) -> Optional[float]:
    match = _MONEY_PATTERN.search(text)
    if not match:
        return None
    digits = match.group(1).replace(",", "")
    if not digits or digits in {".", "-", "-."}:
        return None
    try:
        amount = float(digits)
    except ValueError:
        return None
    if amount > 0 and _PARENTHESISED_NEGATIVE.search(text):
        return -amount
    return amount


# Alphanumeric with optional separators — deliberately permissive on
# character class (invoice numbers vary the most across vendors, per the
# research) but bounded on length so an obviously-wrong grab (a whole
# sentence, a page number alone) is rejected.
_INVOICE_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{1,29}$")


def is_plausible_invoice_number(text: str) -> bool:
    candidate = text.strip()
    if not candidate or candidate.isdigit() and len(candidate) <= 2:
        # A bare 1-2 digit number is almost always something else that got
        # grabbed (a page number, an item count) rather than a real invoice
        # number — real invoice numbers are longer or have a prefix/format.
        return False
    return bool(_INVOICE_NUMBER_PATTERN.match(candidate))


# Pakistan NTN: numeric, commonly 7 digits with an optional trailing check
# digit separated by a dash (e.g. "3947261-8"). Validated as a structural
# shape, not a checksum — this project has no authoritative NTN checksum
# algorithm to validate against, and the research doc doesn't specify one.
_NTN_PATTERN = re.compile(r"^\d{6,7}-?\d?$")


def is_plausible_ntn(text: str) -> bool:
    return bool(_NTN_PATTERN.match(text.strip()))
