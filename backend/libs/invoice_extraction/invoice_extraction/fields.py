"""Label-anchored header-field extraction — Rule 3.1. The primary method for
every single-value field (vendor, invoice number, date, NTN, totals): locate
a known label, then read the value from its textual neighborhood, rather
than running a regex blind against the whole document.

Matching happens at word level with x-position awareness, not on a flat
joined-text string — found live, against a real sample invoice, why this
matters: a genuine two/three-column header layout ("Bill From" / "Invoice
No." / "Currency" side by side) lands on the *same visual row* once words
are grouped by y-position (lines.py), and a plain substring search after
the label would silently swallow the next column's label or value as if it
were this field's own. Word positions are what let this tell "more of the
same value" apart from "a different column bled onto this row."
"""
import re
from typing import Optional

from ocr import PositionedWord

from invoice_extraction.arithmetic import total_reference_amount
from invoice_extraction.candidates import Candidate, SelectionResult, select_candidate
from invoice_extraction.labels import LABEL_VARIANTS, NON_INVOICE_DATE_LABEL_PHRASES, NON_TOTAL_FIELD_LABEL_PHRASES
from invoice_extraction.lines import line_bbox, line_text
from invoice_extraction.validators import find_date_in_text, is_day_month_ambiguous, parse_money

# A horizontal gap bigger than this between two adjacent words is treated as
# a column boundary, not normal word spacing within one value — calibrated
# against real invoices seen during development, where genuine same-column
# gaps ran under 15pt and cross-column jumps ran 200pt+; 50pt sits with
# margin on the "definitely a new column" side without being so tight it
# would break a longer multi-word value.
_COLUMN_GAP_THRESHOLD = 50.0

# Bare header nouns that show up immediately after another field's label in
# a packed multi-column layout (see module docstring's real example —
# "Invoice No." directly followed by "Currency" on the same row, close
# enough together that gap-detection alone doesn't separate them) but are
# never themselves a field value. Same maintenance philosophy as
# labels.LABEL_VARIANTS — grow this list from real failures, not upfront
# guessing.
_NON_VALUE_TOKENS = {
    "currency", "status", "reference", "page",
    # Section headings that sit in a neighbouring column on the same visual
    # row as a field's own label. Real example, found live: a "BILLED TO"
    # heading and a "PAYMENT & SHIPPING" heading are side by side, so the
    # remainder after "BILLED TO" on that row is the *other* section's
    # heading — which became the extracted customer name until these were
    # recognised as headings that are never themselves a value.
    "payment", "shipping", "billed", "billing", "address", "contact", "details",
}


def _tokenize(variant: str) -> list[str]:
    return variant.lower().split()


def _clean(word_text: str) -> str:
    # "#" is deliberately not in the strip set — some real documents glue it
    # directly onto a colon ("#:"), and stripping both would erase the "#"
    # entirely, breaking a match against the literal "#" token some label
    # variants use (e.g. "invoice #").
    return word_text.lower().strip(":-.\t")


def _find_label_word_span(line: list[PositionedWord], tokens: list[str]) -> Optional[int]:
    """Returns the index right after the label's last matched word, or
    None. Matches on cleaned individual words (not a joined substring), so
    "NO." correctly matches the token "no" despite the trailing period."""
    n = len(tokens)
    for start in range(len(line) - n + 1):
        window = [_clean(w.text) for w in line[start:start + n]]
        if window == tokens:
            return start + n
    return None


def _truncate_at_column_gap(words: list[PositionedWord]) -> list[PositionedWord]:
    if not words:
        return []
    result = [words[0]]
    for prev, cur in zip(words, words[1:]):
        if cur.x0 - prev.x1 > _COLUMN_GAP_THRESHOLD:
            break
        result.append(cur)
    return result


_ALL_LABEL_TOKEN_SEQUENCES: list[list[str]] = [
    _tokenize(variant) for variants in LABEL_VARIANTS.values() for variant in variants
]


def _truncate_at_next_label(words: list[PositionedWord]) -> list[PositionedWord]:
    """A same-line remainder can legitimately have a large visual gap
    before it (a right-aligned totals value is the common case — "Total"
    left-aligned, "$100.00" flush right, found live on a real invoice) so
    gap size alone can't be the same-line cutoff signal the way it is for
    the next-line case below. What *does* reliably mark a boundary is
    another field's own label — or a bare header noun like "Currency" that
    is never itself a value (_NON_VALUE_TOKENS) — starting partway through
    the remainder. Both a packed multi-column header row ("Bill From:
    Invoice No. Currency") and a value row with a trailing neighboring
    column ("M180362221 SGD", both found live) need exactly this cut."""
    for start in range(len(words)):
        if _clean(words[start].text) in _NON_VALUE_TOKENS:
            return words[:start]
        for tokens in _ALL_LABEL_TOKEN_SEQUENCES:
            n = len(tokens)
            if start + n > len(words):
                continue
            if [_clean(w.text) for w in words[start:start + n]] == tokens:
                return words[:start]
    return words


def _value_text(words: list[PositionedWord]) -> str:
    if not words or _clean(words[0].text) in _NON_VALUE_TOKENS:
        return ""
    return " ".join(w.text for w in words).strip(" :#-\t")


def find_label_anchored_text(
    lines: list[list[PositionedWord]], field: str, skip_lines: Optional[set[int]] = None,
) -> Optional[tuple[str, int, tuple[float, float, float, float]]]:
    """Returns (value_text, page, bbox) for the first label match, or None.

    `skip_lines` excludes specific row indices from being *matched as a
    label row* — they can still supply a next-line value. Its real use is
    the line-item table's own header row: a column header like "SUBTOTAL"
    is the same word as the totals-section label "Subtotal", and the
    header row comes first on the page, so without this a totals lookup
    anchors to the column header and reads the first line item's amount
    instead of the actual subtotal (a real regression caught live once
    row grouping improved enough for the header row to be read correctly).

    Same-line remainder (the label's own row, after its matched words) is
    tried first — the common "Label: value" shape, including a
    right-aligned value with a large visual gap before it (a totals column
    is the common case) — truncated only at another field's label or a bare
    header noun (_truncate_at_next_label), not at gap size, since gap size
    alone can't tell "right-aligned value" apart from "next column bled in"
    on this axis. Otherwise this falls back to the next line, reading only
    the words horizontally aligned with (at or right of) the label's own
    x-position — the column the label's value actually lives in, not the
    whole next line regardless of which column it's from — truncated at
    both a large gap *and* the same label/non-value-token boundary.
    """
    variants = sorted(LABEL_VARIANTS.get(field, []), key=lambda v: len(v.split()), reverse=True)
    for i, line in enumerate(lines):
        if skip_lines and i in skip_lines:
            continue
        for variant in variants:
            tokens = _tokenize(variant)
            after_idx = _find_label_word_span(line, tokens)
            if after_idx is None:
                continue

            label_start_idx = after_idx - len(tokens)
            label_x0 = line[label_start_idx].x0

            remainder = _truncate_at_next_label(line[after_idx:])
            same_line_value = _value_text(remainder)
            if same_line_value:
                return same_line_value, line[0].page, line_bbox(remainder)

            if i + 1 < len(lines):
                next_line = lines[i + 1]
                aligned = sorted((w for w in next_line if w.x0 >= label_x0 - 5), key=lambda w: w.x0)
                value_words = _truncate_at_next_label(_truncate_at_column_gap(aligned))
                next_line_value = _value_text(value_words)
                if next_line_value:
                    return next_line_value, next_line[0].page, line_bbox(value_words)

            # This line carries this field's label, but no value could be read
            # from it — move to the next *line* rather than retrying this one
            # with the field's shorter synonyms. Those synonyms overlap the
            # label already matched ("billed to" contains "to"), so retrying
            # only re-anchors on a fragment of the same words at a worse
            # offset. Found live: "BILLED TO" correctly matched "billed to",
            # produced nothing, then fell through to the bare "to" variant,
            # which anchored mid-label and returned the neighbouring column's
            # sub-heading as the customer name.
            break
    return None


# Below this mean per-word OCR confidence, a candidate positional-fallback
# line (vendor name or a bare date, neither anchored to a label) is treated
# as too unreliable to prefer — found live on real Pakistani petty-cash
# receipt photos: a handwritten/stamped filing annotation in the corner
# ("Bill-4", garbled by OCR into things like "Bial-1", "BiQ-3", "B0-9")
# consistently landed at 0.49-0.78 confidence, while the actual printed
# vendor name and date on the same receipts landed at 0.92-0.995 — a wide,
# reliable margin between "genuinely printed content" and "noisy
# handwriting/stamp" on this real dataset. 0.85 sits with room on both
# sides of that observed gap.
_MIN_POSITIONAL_FALLBACK_CONFIDENCE = 0.85

# Prefixes seen live on real Pakistani POS receipts that print a
# reference/compliance number as the very first line on the page, ahead of
# the actual business name — FBR's mandatory e-invoice number is the
# concrete example that forced this (it starts every receipt from an
# FBR-integrated POS, e.g. "FBRInvoice#.:147160FFLP23522851"). Never
# itself a vendor name.
_NON_VENDOR_LINE_PREFIXES = (
    "fbrinvoice", "fbr invoice", "invoice#", "invoice #", "bill#", "bill #",
    "receipt#", "receipt #", "pos invoice",
)

# A generic document-title word printed ahead of (sometimes on the very same
# visual row as) the vendor's own name — universal invoice/receipt template
# vocabulary, not tied to any vendor, industry, or country. Found live: a
# hand-designed invoice template printed "INVOICE" immediately before the
# business name on one row ("INVOICE The Florist by Aimen Tahir"), which the
# positional fallback returned whole, title word and all. Stripped from the
# *front* of a candidate line only — never removed from mid-line, since a
# real business name essentially never legitimately starts with one of these
# words. Deliberately excludes single-word "bill"/"receipt" used as a bare
# title on their own line — no evidence of that shape yet, and "Bill" alone
# collides with a common first name ("Bill Smith Consulting"); only the
# unambiguous multi-word/compound forms are included until real evidence
# justifies more.
_DOCUMENT_TYPE_LABELS: tuple[str, ...] = ("tax invoice", "invoice", "cash receipt", "cash memo", "voucher")


def _strip_leading_document_type_label(words: list[PositionedWord]) -> list[PositionedWord]:
    for label in sorted(_DOCUMENT_TYPE_LABELS, key=len, reverse=True):
        tokens = _tokenize(label)
        n = len(tokens)
        if len(words) > n and [_clean(w.text) for w in words[:n]] == tokens:
            return words[n:]
    return words


def _looks_like_a_bare_document_type_label(text: str) -> bool:
    """True when the *entire* candidate line is nothing but a document-
    type title ("Cash Receipt", "Tax Invoice") with no name printed after
    it at all — found live: a standalone "Cash Receipt" line was returned
    as the vendor two lines above the real business name. Deliberately
    separate from _strip_leading_document_type_label, which only ever
    strips this vocabulary from the *front* of a longer line (removing it
    here as well would leave an empty candidate rather than relieving a
    real name of an unwanted prefix) — this instead rejects the candidate
    outright when the label is truly all there is. Universal invoice/
    receipt template vocabulary, not tied to any one vendor."""
    cleaned = text.strip().lower().rstrip(".!")
    return cleaned in {label.lower() for label in _DOCUMENT_TYPE_LABELS}


#: A leading token that starts with a currency word/symbol is a monetary
#: stamp regardless of what OCR did to the digits after it — no real
#: business name is itself printed as its own first character(s) matching
#: a bare currency prefix. General across the same Rs/PKR/USD/$ vocabulary
#: already used for the total field's own amount detection (_LEADING_CURRENCY
#: below), not any one vendor.
#:
#: P1-B (Golden Dataset error analysis): the digit-ratio check below,
#: on its own, misses a stamp whenever OCR corrupts one of the digits into a
#: letter — "RsS40" (a real live case: "Rs.540" or similar, misread) is only
#: 2 of its 5 alnum characters digits (0.4 ratio), comfortably under the 0.5
#: threshold, so the stamp rode along into "RsS40 Express Mart" unremoved.
#: The prefix itself is never ambiguous the way a bare digit run is, so
#: checking for it directly closes this gap without depending on how many of
#: the digits after it survived OCR intact.
_LEADING_CURRENCY_STAMP = re.compile(r"^(?:rs\.?|pkr|usd|\$)", re.IGNORECASE)


def _strip_leading_reference_stamp(words: list[PositionedWord]) -> list[PositionedWord]:
    """A stamped reference/amount marker sometimes sits on the very same
    visual row as the real vendor name, immediately ahead of it — a
    corner annotation ("Rs1100", "R8270", a garbled "RsS40") printed
    inline rather than on its own line. Found live on three independent
    real receipts: the whole line clears `_looks_like_reference_number`
    (the real vendor's own letters dilute the line's overall digit ratio
    comfortably under 50%), so the noise token rides along into the
    returned vendor text unless removed here.

    Two independent structural tests, either one enough to strip the word:
    the digit-dominant-token test `_looks_like_reference_number` already
    applies to a whole line, applied here to only the line's own first
    word; or the word starts with a bare currency prefix
    (_LEADING_CURRENCY_STAMP), which catches a stamp even when OCR mangled
    enough of its digits to defeat the ratio check on its own. Drops at
    most that one leading word, and only when the line has more than one
    word left over afterward: a business name is never itself reduced to
    nothing by this, only ever relieved of a stamp printed ahead of it."""
    if len(words) < 2:
        return words
    first_text = words[0].text
    if _LEADING_CURRENCY_STAMP.match(first_text):
        return words[1:]
    alnum = [c for c in first_text if c.isalnum()]
    if not alnum:
        return words
    digit_ratio = sum(c.isdigit() for c in alnum) / len(alnum)
    if digit_ratio > 0.5:
        return words[1:]
    return words


def _truncate_trailing_label_fragment(words: list[PositionedWord]) -> list[PositionedWord]:
    """A field label glued onto the *tail* of the same visual row as the
    real vendor name, rather than sitting on its own separate line — found
    live (P1-B Golden Dataset error analysis): OCR grouped "Date:" (misread
    as "ate:", the leading "D" lost) onto the very same row as "Express
    Mart", one single line reading "Express Mart ate:" end to end, not two
    rows that a continuation-merge would need to combine.

    A trailing token ending in ":" always marks the *start* of a new
    field's own label, regardless of which label it is or how badly OCR
    corrupted its spelling — deliberately not matched against the known
    label vocabulary (_truncate_at_next_label, used elsewhere in this
    module) the way _truncate_at_registration_number's NTN check is,
    because that match would fail here precisely when it matters most: OCR
    corrupted "Date" past recognition, so only the colon survived as a
    reliable signal. Drops at most the line's own last word, and only when
    something is left over afterward, so a business name is never reduced
    to nothing by this."""
    if len(words) > 1 and words[-1].text.rstrip().endswith(":"):
        return words[:-1]
    return words


# A Pakistani business's own NTN (National Tax Number) registration marker
# — a fixed, government-mandated label, never itself part of a business's
# name — sometimes prints on the very same line as the vendor name rather
# than its own separate line ("KFC Bahia Pindl Phase-7 (0154) NTN#0819531-5",
# found live). Truncating the candidate at this marker is safe regardless of
# which vendor prints it: a real business name never legitimately contains
# the literal substring "NTN" followed by a registration number.
_MID_LINE_REGISTRATION_MARKER = re.compile(r"\bNTN\s*#?\s*\d", re.IGNORECASE)


def _truncate_at_registration_number(text: str) -> str:
    match = _MID_LINE_REGISTRATION_MARKER.search(text)
    return text[:match.start()].strip() if match else text


# Two more structural, never-vendor-specific line shapes found live on real
# receipts, both matched only when they describe the line's *entire* cleaned
# text (not a substring anywhere in it) — the same conservative standard
# _NON_VALUE_TOKENS already holds itself to elsewhere in this module, so a
# real business name that happens to contain one of these words in passing
# is never at risk of being rejected.
#
# "Software/provider credit" — a POS system or e-commerce platform
# advertising itself on the receipt it printed, always as a leading phrase
# ("Powered By LucrumX", "Software By Pasha Tech", "Solution Provided By
# IPOS") — universal POS/software vocabulary, seen across multiple unrelated
# vendors in this dataset, never itself the business that issued the
# receipt.
_PROVIDER_CREDIT_PREFIXES: tuple[str, ...] = (
    "powered by", "software by", "solution provided by", "system by", "developed by",
)

# "Order-fulfillment metadata" — a closed, industry-wide vocabulary used by
# point-of-sale and food-delivery systems to record how an order was
# fulfilled, never a business's own name. Matched as a whole line so a real
# vendor legitimately named e.g. "ABC Pickup & Delivery Services" is
# unaffected (that line would still contain other words rejecting the
# whole-line match).
_ORDER_FULFILLMENT_PHRASES: frozenset[str] = frozenset({
    "pickup", "delivery", "pickup/delivery", "dine in", "takeaway", "take away", "walk in", "eat in",
    "home delivery",
})

# P1 (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md's audit —
# "bank-screenshot account-holder confusion" was catalogued as one of
# vendor's structurally-hard remaining failures): a label naming the
# document's *recipient* (a bank statement's account holder, a bill's
# addressee) rather than its issuer. Structurally general, not tied to any
# one document or bank: whoever a document says money is FOR is never the
# same party as who it was paid TO, and this shape — a fixed prefix
# labeling a person/entity as the account/bill holder — is common across
# any bank statement or invoice template, not one vendor's own wording.
#: "bill to"/"ship to" (P1-B) reuse the exact same phrases LABEL_VARIANTS'
#: own "customer_label" already recognizes for the *customer* field
#: elsewhere in this module — the identical semantic role (who a document's
#: amount is FOR), not a new, separately-invented vocabulary. Deliberately
#: NOT the bare "to"/"customer" that customer_label also lists: standing
#: alone, both are short enough to plausibly start a real business name's
#: own first word by coincidence, and no live failure justifies that added
#: false-positive risk (see step 11's own instruction to prefer scored
#: evidence over hard rejection wherever the signal is not unambiguous).
_CUSTOMER_OR_ACCOUNT_HOLDER_PREFIXES: tuple[str, ...] = (
    "account holder", "account title", "a/c title", "customer name", "bill to", "ship to",
)

#: A personal letter/email salutation, addressed to the document's own
#: *recipient* — found live: once an email-address candidate is correctly
#: rejected (see _looks_like_an_email_address below), "Dear Muhammad,"
#: printed just below it was the next thing that would otherwise win.
#: Structurally the same broad category the customer/account-holder
#: prefixes above already cover (who a document is addressed TO, never who
#: issued it), universal across any letter or email format regardless of
#: which platform generated it, never a business's own display name.
_GREETING_SALUTATION_PATTERN = re.compile(r"^\s*(?:dear|hi|hello)\b", re.IGNORECASE)


def _looks_like_a_greeting_salutation(text: str) -> bool:
    return bool(_GREETING_SALUTATION_PATTERN.match(text.strip()))


def _looks_like_decorative_or_metadata_text(text: str) -> bool:
    """True for a line that is boilerplate the receipt itself prints — a
    software/POS provider's own self-credit, a courtesy footer, or an
    order-fulfillment field — rather than the issuing business's name. See
    the constants above for what each category covers and why it
    generalizes past this dataset."""
    cleaned = text.strip().lower().rstrip(".!")
    if any(cleaned.startswith(p) for p in _PROVIDER_CREDIT_PREFIXES):
        return True
    if cleaned in _ORDER_FULFILLMENT_PHRASES:
        return True
    # Courtesy footers ("Thank you, please visit again.") — matched as a
    # substring rather than a strict prefix since real receipts glue a
    # customer's own name or a stray character in front of this exact
    # phrase, but it is never itself preceded by anything that could be a
    # business name (it is boilerplate start to finish).
    return "thank you" in cleaned and ("visit again" in cleaned or "come again" in cleaned)


def _looks_like_a_customer_or_account_holder_field(text: str) -> bool:
    """True for a line naming who a document's amount is FOR, never who it
    was paid TO — see _CUSTOMER_OR_ACCOUNT_HOLDER_PREFIXES's own docstring
    for why this generalizes past any one document."""
    cleaned = text.strip().lower()
    return any(cleaned.startswith(p) for p in _CUSTOMER_OR_ACCOUNT_HOLDER_PREFIXES)


#: A whole-line email address — e.g. a delivery-platform receipt's own
#: support/no-reply contact address printed near the top ("papajohns@
#: livepepper.com", found live: a real vendor's display name was rejected in
#: favor of exactly this on one Golden Dataset document). Universal internet
#: syntax, not tied to any one business or platform: a business's own
#: display name is never itself formatted as `local-part@domain.tld`, so
#: this is safe to reject outright rather than merely down-weight.
_EMAIL_ADDRESS_PATTERN = re.compile(r"^\S+@\S+\.\S+$")


def _looks_like_an_email_address(text: str) -> bool:
    return bool(_EMAIL_ADDRESS_PATTERN.match(text.strip()))


#: P1-B (Golden Dataset error analysis): a street/building address, printed
#: as the top-of-page candidate ahead of the real vendor name, was returned
#: whole ("Plaza 229,Third floor, Hotel view square, Bahria Spring", found
#: live) — comma-separated address components are a near-universal
#: convention (the same reasoning _is_name_continuation already applies when
#: deciding whether a *second* line continues a name), but comma alone is
#: too broad a signal on its own for a whole candidate line — a real
#: business legitimately punctuated with a comma ("7-Eleven, Inc.") must not
#: be caught. Gated on a comma AND at least one word from this universal
#: address-structure vocabulary (building/floor/street-level terms common
#: across many countries' postal addressing, not one country's or vendor's
#: own wording) keeps the false-positive risk low while still catching the
#: observed failure shape.
_ADDRESS_STRUCTURE_WORDS: frozenset[str] = frozenset({
    "floor", "plaza", "street", "road", "avenue", "block", "phase", "square", "building",
    "colony", "sector",
})


def _looks_like_an_address_line(text: str) -> bool:
    cleaned = text.strip().lower()
    if "," not in cleaned:
        return False
    words = re.findall(r"[a-z]+", cleaned)
    return any(w in _ADDRESS_STRUCTURE_WORDS for w in words)


def _looks_like_reference_number(text: str) -> bool:
    """True for a line that is a reference/serial number — or a date, or a
    field label — rather than a name: one of the known receipt-header
    prefixes above, a line whose alphanumeric content is more than half
    digits (a real business name is letter-dominated; "Rs 400", "B0-9",
    "39505" are not), or a line that is itself a recognizable date. That
    last check was added live against a real regression: a native-PDF
    billing statement's own "Date issued Jul 28, 2026 Jul 28, 2026" header
    line has a roughly even letter/digit mix (so the digit-ratio check
    alone let it through) and 1.0 OCR confidence (native PDF text always
    is, so the confidence floor can't catch it either) — it was picked as
    the "vendor name" ahead of the real one two lines below ("Shopify
    Commerce Singapore Pte. Ltd."). A vendor name is essentially never
    itself a date string, so this generalizes past just this one document.
    """
    cleaned = text.strip().lower()
    if any(cleaned.startswith(p) for p in _NON_VENDOR_LINE_PREFIXES):
        return True
    if find_date_in_text(text) is not None:
        return True
    alnum = [c for c in cleaned if c.isalnum()]
    if not alnum:
        return True
    digit_ratio = sum(c.isdigit() for c in alnum) / len(alnum)
    return digit_ratio > 0.5


def _line_confidence(line: list[PositionedWord]) -> float:
    return sum(w.confidence for w in line) / len(line) if line else 0.0


# A continuation line is accepted only when it sits close enough below the
# winning line to plausibly be the *same* printed name wrapped onto a second
# row — scaled to the winning line's own text height rather than a fixed
# point value, since this pipeline's coordinate scale varies with source
# resolution/DPI while a business name's two lines are always set in the
# same font. Calibrated against a real receipt found live, and it is a
# generous bound, not the primary discriminator — measured on a real
# hardware-store invoice, line-to-line spacing in a tight single-column
# receipt is nearly identical whether the next line is a genuine name
# continuation or the following, unrelated address line, so *content*
# (digit-free vs. digit-bearing, see below) is what actually separates the
# two; this bound only guards against merging something meaningfully
# further down the page after a real section gap.
_MAX_CONTINUATION_GAP_RATIO = 1.75

# Merging is a compounding decision — it trusts *two* uncertain OCR reads
# together instead of one — so it holds itself to a stricter floor than
# simply accepting a single top-of-page candidate. Found live why the plain
# floor isn't enough: a garbled two-word OCR fragment ("wn Rahria", actually
# noise from a logo/watermark) landed at 0.852 confidence — clearing
# _MIN_POSITIONAL_FALLBACK_CONFIDENCE by a hair — and passed every other
# check (digit-free, comma-free, high enough, close enough), merging onto an
# unrelated line above it. Both real continuations seen live ("Electric &
# Hardware Store", "Aimen Tahir") read at 0.96-0.97; 0.90 sits between that
# and the garbled fragment's 0.852 with margin on both sides.
_MIN_CONTINUATION_CONFIDENCE = 0.90


def _line_height(line: list[PositionedWord]) -> float:
    return max(w.y1 for w in line) - min(w.y0 for w in line)


def _is_name_continuation(prev_line: list[PositionedWord], candidate_line: list[PositionedWord]) -> bool:
    """True when `candidate_line` reads as the same business name wrapping
    onto a second row right after `prev_line` (see _MAX_CONTINUATION_GAP_RATIO
    for the spacing rule). Never a reference number, decorative/metadata
    line, bare document-type label, or digit-bearing line: a genuine
    business-name continuation is letter-and-punctuation only in every real
    example seen (a hardware store's "Electric & Hardware Store", a
    florist's "Aimen Tahir") — whereas the next *unrelated* line on a
    receipt (a shop number, an address, a phone number) essentially always
    carries a digit.

    Also never a comma-bearing line. Found live: a digit-free address
    fragment ("Hotel view square, Bahria Spring") passed every check above
    and was wrongly merged onto a bank-app screenshot's own account-holder
    name — an address is not reliably digit-bearing on its own, but a
    business-name suffix ("Electric & Hardware Store", "Traders",
    "Enterprises", "& Sons") is essentially never comma-separated, while
    listing address components with commas is a near-universal convention.
    This trades away the rare comma-bearing legal name (e.g. "Smith, Jones &
    Co.") for materially safer behaviour on the far more common case of an
    address line sitting directly below the vendor name."""
    text = line_text(candidate_line).strip()
    if not text or any(c.isdigit() for c in text) or "," in text:
        return False
    if _looks_like_reference_number(text) or _looks_like_decorative_or_metadata_text(text):
        return False
    # A trailing colon marks a field *label* ("Date:", "Time:", "Order:"),
    # never a business-name suffix — found live: OCR misread "Date:" as just
    # "ate:" (the leading "D" lost), which is digit-free and comma-free and
    # so passed every check above, merging onto a real vendor name as if it
    # were a second line of the business's own name ("Express Mart ate:").
    # A genuine name continuation is never itself shaped like a label, so
    # this generalizes past this one document/label.
    if text.rstrip().endswith(":"):
        return False
    if not line_text(_strip_leading_document_type_label(candidate_line)).strip():
        return False
    if _line_confidence(candidate_line) < _MIN_CONTINUATION_CONFIDENCE:
        return False
    # Real OCR boxes commonly overlap slightly between genuinely distinct
    # lines (bounding-box padding around a short word can extend past the
    # next line's own top) — found live: "Azeem"'s box outlasts "Electric &
    # Hardware Store"'s own top by several points despite the two being
    # unambiguously separate printed rows. Clamped to zero rather than
    # rejected outright: an overlap is not evidence *against* the next line
    # being an immediate continuation, only a gap meaningfully larger than
    # the line's own height is.
    gap = max(0.0, candidate_line[0].y0 - max(w.y1 for w in prev_line))
    height = _line_height(prev_line)
    return height > 0 and gap <= height * _MAX_CONTINUATION_GAP_RATIO


def _is_rejected_vendor_candidate(text: str) -> bool:
    return (
        _looks_like_reference_number(text) or _looks_like_decorative_or_metadata_text(text)
        or _looks_like_a_customer_or_account_holder_field(text) or _looks_like_an_email_address(text)
        or _looks_like_an_address_line(text) or _looks_like_a_bare_document_type_label(text)
        or _looks_like_a_greeting_salutation(text)
    )


#: P1 (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md §6/§19):
#: how far a winning positional vendor candidate must beat its nearest
#: rival to count as a confident ACCEPT rather than REVIEW. Deliberately
#: far smaller than the gap between "clears the confidence floor" and
#: "doesn't" (~1.5 at these constants' scale, see _vendor_candidate_
#: evidence's own comment) — this only ever fires for a genuine near-tie
#: (two candidates whose combined evidence — confidence, position,
#: font size, known-vendor match — happened to land almost exactly
#: together), never for an ordinary confidence-tier or noise-filter
#: difference, which is always much larger than this.
_VENDOR_AMBIGUITY_MARGIN = 0.05

#: The two-tier structure find_vendor_name has always had (Rule 3.4):
#: any candidate clearing the OCR-confidence floor outranks every
#: candidate that doesn't, regardless of exactly how confident either one
#: is beyond/below that floor. Expressed here as an explicit floor-tier
#: base score (10.0) versus the continuous 0.0-8.5 range a sub-floor
#: candidate's own confidence*10 can reach — the ~1.5-point gap between
#: them is deliberately far larger than any single evidence bonus below
#: (font size, known-vendor match, document order), so no combination of
#: those can ever let a sub-floor candidate outrank a floor-clearing one,
#: exactly preserving the original tier boundary.
_VENDOR_FLOOR_TIER_SCORE = 10.0

#: A tiny bonus for appearing earlier on the page — vendor is
#: conventionally the most prominent top-of-page text (Rule 3.4), but this
#: is deliberately far too small a contribution to ever decide a case on
#: its own; it only ever breaks a genuine tie between candidates whose
#: other evidence landed identically.
_VENDOR_ORDER_EPSILON = 0.001

#: A confirmed-rejected candidate (looks like a reference number, or
#: decorative/metadata text) scores far below anything else on this
#: file's scale — it can still win a tie-break among other equally-
#: rejected candidates (reproducing the original "return the first line
#: even if everything looks like noise" guarantee), but can never
#: outrank any non-rejected candidate.
_VENDOR_REJECTED_SCORE = -1000.0

#: A modest, deliberately small bonus for a candidate whose text fuzzy-
#: matches a company's own known vendor list (dedup.find_matching_vendor)
#: — soft evidence a real vendor has been seen before, per P1's own
#: instruction (§15 of the audit's task) that this must never become an
#: unconditional override, only one more named contribution to the score.
_VENDOR_KNOWN_MATCH_BONUS = 0.3

#: font_size is declared on PositionedWord and would be real, useful
#: evidence for a native-PDF invoice (a vendor's name is usually printed
#: larger than its address/contact block) — but as of this audit, no OCR
#: or PDF-text extraction path in this codebase actually populates it
#: (confirmed: `grep -rn "font_size=" ocr/ invoice_extraction/` finds only
#: the field's own declaration in ocr/models.py). This bonus is real
#: architecture, not dead code — it activates the moment a future OCR/PDF
#: change starts reporting real font sizes, with zero further change
#: needed here — but it is honestly inert against every document this
#: pipeline can currently see, including the entire Golden Dataset (all
#: 35 are photographed/scanned images, which never carry a native font
#: size at all).
_VENDOR_FONT_SIZE_BONUS_CAP = 0.5


def _vendor_candidate_evidence(
    index: int, line: list[PositionedWord], text: str, known_vendors: Optional[list[str]],
) -> dict[str, float]:
    """Every reason a single top-of-page line might or might not be the
    vendor, as named, signed contributions — the P1 candidate-evidence
    step. Rejected candidates (reference-number-shaped or decorative/
    metadata text) get no other evidence at all: a stamp or a courtesy
    footer isn't "a weak vendor," it's disqualified outright, exactly as
    the original filter-then-skip loop already treated it.
    """
    if _is_rejected_vendor_candidate(text):
        return {
            "negative:looks_like_reference_or_decorative": _VENDOR_REJECTED_SCORE,
            "document_order": -index * _VENDOR_ORDER_EPSILON,
        }

    confidence = _line_confidence(line)
    evidence: dict[str, float] = {
        "ocr_confidence": (
            _VENDOR_FLOOR_TIER_SCORE if confidence >= _MIN_POSITIONAL_FALLBACK_CONFIDENCE
            else confidence * _VENDOR_FLOOR_TIER_SCORE
        ),
        "document_order": -index * _VENDOR_ORDER_EPSILON,
    }
    font_sizes = [w.font_size for w in line if w.font_size is not None]
    if font_sizes:
        # Deliberately capped, not a raw font-size number — a candidate
        # with a much larger font is somewhat more likely to be the
        # document's own prominent header text, but "large font" is not
        # itself proof of anything, so this is bounded well below the
        # floor-tier gap the same way every other bonus here is.
        evidence["font_size"] = min(_VENDOR_FONT_SIZE_BONUS_CAP, (sum(font_sizes) / len(font_sizes)) / 100.0)
    if known_vendors:
        from invoice_extraction.dedup import find_matching_vendor

        if find_matching_vendor(text, known_vendors):
            evidence["known_vendor_match"] = _VENDOR_KNOWN_MATCH_BONUS
    return evidence


def _finish_vendor_candidate(
    lines: list[list[PositionedWord]], index: int, line: list[PositionedWord], text: str,
) -> tuple[str, tuple[float, float, float, float]]:
    """Folds in the winning candidate's continuation line, when its
    immediately-following line reads as the same business name wrapping
    onto a second printed row (_is_name_continuation) — applied only to
    whichever candidate the selection step below actually chose, exactly
    as the original single-pass loop already did."""
    if index + 1 < len(lines) and _is_name_continuation(line, lines[index + 1]):
        return f"{text} {line_text(lines[index + 1]).strip()}", line_bbox(line + lines[index + 1])
    return text, line_bbox(line)


def _vendor_selection(
    lines: list[list[PositionedWord]], top_fraction: float, known_vendors: Optional[list[str]],
) -> SelectionResult:
    """The real implementation behind find_vendor_name — returns the full
    P1 SelectionResult (candidates.py) rather than collapsing straight to
    a bare tuple, so a caller that wants the winner's evidence/ambiguity
    status (extract_invoice.py's _vendor_field) can see it, while
    find_vendor_name's own public contract below stays exactly the 4-tuple
    every existing caller already expects.

    Label-anchored is still a hard early return, never routed through
    candidate scoring at all — this is a deliberate, pre-existing design
    choice (Rule 3.1/3.4: a label match is already the strongest tier,
    with no separate value-pattern check for a name to weigh against
    anything else), not something this P1 pass changes.
    """
    anchored = find_label_anchored_text(lines, "vendor_label")
    if anchored:
        text, page, bbox = anchored
        winner = Candidate(
            field_name="vendor_name", value=text, method="label_anchor+pattern", page=page, bbox=bbox,
            evidence={"label_anchor_match": 1.0},
        )
        return SelectionResult(winner=winner, status="accept", margin=None, candidates=[winner])

    page_zero_heights = [w.y1 for line in lines if line[0].page == 0 for w in line]
    if not page_zero_heights:
        return SelectionResult(winner=None, status="unknown", margin=None, candidates=[])
    max_y = max(page_zero_heights)
    cutoff = max_y * top_fraction
    candidate_positions = [
        i for i, line in enumerate(lines)
        if line[0].page == 0 and line[0].y0 <= cutoff and line_text(line).strip()
    ]
    if not candidate_positions:
        return SelectionResult(winner=None, status="unknown", margin=None, candidates=[])

    # Each raw candidate is (index_in_lines, original_line, stripped_text)
    # — the index is what continuation-matching looks up the next line
    # with, the original line is what its bbox/confidence key off, and
    # every content check runs against the cleaned text: a leading
    # document-type label (_strip_leading_document_type_label), then a
    # leading reference/amount stamp (_strip_leading_reference_stamp)
    # stripped word-for-word, then a trailing label fragment glued onto the
    # same row (_truncate_trailing_label_fragment, P1-B), then a mid-line
    # NTN registration marker (_truncate_at_registration_number) cut from
    # the remaining text. The first three cleanup steps predate P1/were
    # untouched by P1-A — only what happens to the resulting candidates
    # (scoring/selection) changed there.
    raw_candidates: list[tuple[int, list[PositionedWord], str]] = []
    for i in candidate_positions:
        line = lines[i]
        stripped_words = _truncate_trailing_label_fragment(
            _strip_leading_reference_stamp(_strip_leading_document_type_label(line))
        )
        text = _truncate_at_registration_number(line_text(stripped_words).strip())
        if text:
            raw_candidates.append((i, line, text))
    if not raw_candidates:
        return SelectionResult(winner=None, status="unknown", margin=None, candidates=[])

    candidates = [
        Candidate(
            field_name="vendor_name", value=text, method="positional_fallback",
            evidence=_vendor_candidate_evidence(i, line, text, known_vendors),
            context={"index": i, "line": line, "raw_text": text},
        )
        for i, line, text in raw_candidates
    ]
    result = select_candidate(candidates, ambiguity_margin=_VENDOR_AMBIGUITY_MARGIN)
    if result.winner is None:
        return result

    # Apply the continuation-line merge to the winner only — same as the
    # original single-pass loop, which only ever computed this for
    # whichever line it had already decided to return.
    index, line, text = result.winner.context["index"], result.winner.context["line"], result.winner.context["raw_text"]
    finished_text, bbox = _finish_vendor_candidate(lines, index, line, text)
    finished_winner = Candidate(
        field_name="vendor_name", value=finished_text, method="positional_fallback", page=0, bbox=bbox,
        evidence=result.winner.evidence, context=result.winner.context,
    )
    return SelectionResult(winner=finished_winner, status=result.status, margin=result.margin, candidates=result.candidates)


def find_vendor_name(
    lines: list[list[PositionedWord]], top_fraction: float = 0.25, known_vendors: Optional[list[str]] = None,
) -> Optional[tuple[str, int, tuple[float, float, float, float], str]]:
    """Rule 3.4. Label-anchored first (a "Bill From"/"Sold By"/"Vendor"
    label, same mechanism as any other field); falls back to the first
    non-empty line in the top portion of the first page, since the vendor
    name is conventionally the most prominent thing at the top of an
    invoice even when no explicit label is present (confirmed across real
    sample invoices this library was tested against that don't label the
    vendor name explicitly at all — they just put it first).

    As of P1, the positional fallback is implemented as a genuine
    candidate-generation -> evidence -> selection pipeline
    (_vendor_selection, candidates.py) instead of a single filter-then-
    first-acceptable-line loop — see _vendor_candidate_evidence for every
    signal a candidate is scored on (OCR confidence tier, document order,
    font size where available, a known-vendor fuzzy match) and
    _is_rejected_vendor_candidate for the negative evidence (reference-
    number-shaped or decorative/metadata text) that disqualifies a
    candidate outright. The externally-visible behavior is unchanged: a
    label match still wins outright; a candidate clearing the OCR-
    confidence floor still always outranks one that doesn't; nothing
    clearing the floor still falls back to the highest-confidence non-
    noise candidate, and everything looking like noise still falls back to
    the plain first line rather than losing the field outright. What's new
    is that font size and known-vendor matching can now actually influence
    *which* candidate wins a genuine tie, not just add a confidence
    afterthought once a winner was already picked by position alone (see
    find_vendor_candidate_selection below for callers that want that
    reasoning, not just the final text).

    `known_vendors`, when supplied, lets a company's own already-seen
    vendor names act as one more piece of evidence (P1 §15) — never an
    unconditional override, just a modest score bonus alongside every
    other signal.

    Returns (value_text, page, bbox, method) — method is
    "label_anchor+pattern" for the labeled path (there is no separate
    value-pattern check for a name, so a label match is already the
    strongest tier) or "positional_fallback" for the top-of-page guess, so
    callers can feed this straight into confidence.field_confidence without
    re-deriving which path was taken.
    """
    result = find_vendor_candidate_selection(lines, top_fraction=top_fraction, known_vendors=known_vendors)
    if result.winner is None:
        return None
    return result.winner.value, result.winner.page, result.winner.bbox, result.winner.method


def find_vendor_candidate_selection(
    lines: list[list[PositionedWord]], top_fraction: float = 0.25, known_vendors: Optional[list[str]] = None,
) -> SelectionResult:
    """Same underlying logic as find_vendor_name, but returns the full P1
    SelectionResult (candidates.py) — every candidate considered, the
    winner's own named evidence, and whether the win was a confident
    ACCEPT or a thin-margin REVIEW — for a caller that needs to explain or
    act on *why* a vendor was chosen (extract_invoice.py's _vendor_field),
    not just receive its final text.
    """
    return _vendor_selection(lines, top_fraction, known_vendors)


def vendor_candidate_cleared_confidence_floor(candidate: Candidate) -> bool:
    """True when a vendor candidate's own OCR confidence is trustworthy on
    its own terms — for a positional-fallback candidate, whether it cleared
    _MIN_POSITIONAL_FALLBACK_CONFIDENCE (i.e. it would have scored in the
    floor tier even if it had been the *only* candidate ever considered);
    a label-anchored winner (a different method, always the strongest
    tier) always counts as cleared — this only has an opinion about the
    positional-fallback path's own two-tier structure.

    P1-B (Golden Dataset error analysis): select_candidate's own lone-
    candidate rule is unconditional accept, by design (see its own
    docstring) — it answers "did this beat its rivals", never "is this
    trustworthy in absolute terms". A lone sub-floor guess is a real,
    observed failure shape distinct from ordinary ambiguity: a garbled
    corner stamp/reference code ("BiQ-13", found live) that was the *only*
    thing OCR recovered in the header region — the real vendor name was
    never extracted at all — must not be reported with the same full
    confidence as a lone, cleanly-printed vendor name would be. Exposed
    here rather than inlined in extract_invoice.py so the floor-tier
    threshold this compares against stays defined in exactly one place
    (_vendor_candidate_evidence's own constants)."""
    if candidate.method != "positional_fallback":
        return True
    return candidate.evidence.get("ocr_confidence", 0.0) >= _VENDOR_FLOOR_TIER_SCORE


def find_date_anywhere(
    lines: list[list[PositionedWord]],
) -> Optional[tuple[str, int, tuple[float, float, float, float]]]:
    """Positional/pattern fallback for invoice_date, used only when
    find_label_anchored_text finds no "Date:"-style label at all. Real POS
    receipts found live fail the label-anchored path two ways that this
    exists to recover: no date label printed at all (the date just appears
    on its own, e.g. "16 Jun 2026"), or the label glued directly onto its
    own value with no space at all in the source OCR text
    ("Date:29/06/2026.16:29:41" arrives as a single word — the label
    matcher only ever matches whole words, so "date" can never be found
    inside it). Scans every line in document order for a date-shaped
    substring (validators.find_date_in_text handles the "glued" extraction
    itself), skipping low-confidence lines for the same reason
    find_vendor_name does — a garbled stamp string that happens to be
    date-shaped should not be preferred over a clean one elsewhere on the
    page. Returns the first match, not necessarily on page 0: unlike
    vendor name, a printed date has no reliable "always near the top"
    convention to narrow the search to.

    P1-C: `_date_field` (extract_invoice.py) no longer calls this — it uses
    `find_invoice_date_candidate_selection` below instead, which subsumes
    both this function's own positional scan and the label-anchored lookup
    into one evidence-scored candidate pool spanning the *whole* document
    (this function, and the plain label-anchored lookup it complemented,
    each stopped at the first hit — see that function's own docstring for
    why scanning everything, not just the first match, matters). Kept
    here, unmodified, purely because it is still independently tested and
    nothing about its own contract needs to change.
    """
    for line in lines:
        if _line_confidence(line) < _MIN_POSITIONAL_FALLBACK_CONFIDENCE:
            continue
        text = line_text(line).strip()
        if not text:
            continue
        if find_date_in_text(text) is not None:
            return text, line[0].page, line_bbox(line)
    return None


#: P1-C: how far a winning invoice_date candidate must beat its nearest
#: rival to count as a confident ACCEPT rather than REVIEW — the same
#: design as vendor's own _VENDOR_AMBIGUITY_MARGIN (candidates.py's own
#: ambiguity mechanism), sized the same way: far smaller than the gap
#: between any two of this field's own tiers, so it only ever fires for a
#: genuine near-tie.
_DATE_AMBIGUITY_MARGIN = 0.05

#: Three tiers, mirroring vendor's own design (fields.py's
#: _VENDOR_FLOOR_TIER_SCORE) but sized to keep dates' own three signal
#: strengths cleanly separated: a genuine, specific invoice-date label
#: ("Invoice Date", "Date Issued", "Bill Date"...) is the strongest
#: possible evidence a value belongs to *this* field; the bare, generic
#: "Date" label is real label evidence too, but the weakest form of it (it
#: says nothing about which date field this is — every date-bearing field
#: on a template could technically be introduced this way); an unlabeled
#: positional date has no label evidence at all. Gaps between tiers (5.0)
#: are deliberately far larger than any single penalty below (at most
#: -3.0), so no combination of negative evidence can ever let a lower tier
#: outrank a higher one — only decide ties *within* a tier, or how a lone
#: candidate's own confidence is reported.
_DATE_LABEL_ANCHOR_SCORE = 20.0
_DATE_BARE_LABEL_SCORE = 15.0
_DATE_FLOOR_TIER_SCORE = 10.0

#: A tiny bonus for appearing earlier in the document — the same
#: tie-breaking role _VENDOR_ORDER_EPSILON plays for vendor, sized
#: identically.
_DATE_ORDER_EPSILON = 0.001

#: A date-shaped value labeled with a *different* date field's own name
#: (NON_INVOICE_DATE_LABEL_PHRASES — "Due Date", "Order Date", ...) is
#: never the invoice date — this is deliberately as large a penalty as
#: vendor's own rejection score (fields.py's _VENDOR_REJECTED_SCORE): it
#: can still win a tie-break among other equally-disqualified candidates
#: (preserving the "return something rather than nothing" guarantee when a
#: due-date line is genuinely the only date-shaped text anywhere in the
#: document), but can never outrank a real invoice-date candidate.
_DATE_NON_INVOICE_LABEL_PENALTY = -1000.0

#: A genuinely ambiguous numeric date (validators.is_day_month_ambiguous —
#: "06/02/2026", where day-first and month-first both parse to a different,
#: equally-plausible real date) is weaker evidence than an unambiguous one,
#: but never disqualifying — a document where this is the *only* date
#: found should still report it, just with visibly less confidence (see
#: find_invoice_date_candidate_selection's own evidence["ambiguous_format"]
#: flag, consumed by extract_invoice.py's _date_field the same way
#: vendor's own low_confidence flag is). Sized well under a single tier
#: gap (5.0) so it can only ever decide a tie against an equally-tiered,
#: unambiguous rival — never demote a candidate across tiers on its own.
_DATE_AMBIGUOUS_FORMAT_PENALTY = -3.0

_NON_INVOICE_DATE_LABEL_TOKENS: list[list[str]] = [_tokenize(p) for p in NON_INVOICE_DATE_LABEL_PHRASES]


def _line_contains_phrase(line: list[PositionedWord], tokens: list[str]) -> bool:
    words = [_clean(w.text) for w in line]
    n = len(tokens)
    return any(words[i:i + n] == tokens for i in range(len(words) - n + 1))


def _date_candidate_evidence(
    index: int, text: str, is_non_invoice_labeled: bool, base_score: float,
) -> dict[str, float]:
    """Every reason a single date-shaped value might or might not be the
    invoice date, as named, signed contributions — mirrors vendor's own
    _vendor_candidate_evidence in spirit: `base_score` already encodes
    which of the three tiers (see above) this candidate belongs to: a
    genuine invoice-date label, a bare "date" label, or an unlabeled
    positional confidence tier. Negative evidence (a different date
    field's own label, a genuinely ambiguous numeric format) is layered on
    top of whichever tier the candidate is already in, never used to move
    it between tiers."""
    evidence: dict[str, float] = {
        "date_evidence": base_score,
        "document_order": -index * _DATE_ORDER_EPSILON,
    }
    if is_non_invoice_labeled:
        evidence["non_invoice_date_label"] = _DATE_NON_INVOICE_LABEL_PENALTY
    if is_day_month_ambiguous(text):
        evidence["day_month_ambiguous"] = _DATE_AMBIGUOUS_FORMAT_PENALTY
    return evidence


def _invoice_date_candidates(lines: list[list[PositionedWord]]) -> list[Candidate]:
    """Generates a Candidate for every date-shaped value anywhere in the
    document — the P1-C replacement for the old ad-hoc "first label match,
    else first positional match" behavior (find_label_anchored_text +
    find_date_anywhere, each stopping at their own first hit). Scanning the
    *whole* document, not just the first hit, is what actually lets an
    invoice-date-labeled value beat a due-date-labeled one that happens to
    print first (Phase 6's own "the system must not simply select the
    first valid date") — the old single-shot lookup could never do this: it
    physically never saw a second candidate to compare against.

    Per-line logic deliberately mirrors find_label_anchored_text's own
    same-line/next-line value lookup (reusing its exact helpers —
    _find_label_word_span, _truncate_at_next_label, _truncate_at_column_gap,
    _value_text) rather than reimplementing it differently, so this
    generalizes the same way that function already does (packed
    multi-column rows, right-aligned values) instead of regressing on it.
    """
    variants = sorted(LABEL_VARIANTS["invoice_date"], key=lambda v: len(v.split()), reverse=True)
    candidates: list[Candidate] = []

    for i, line in enumerate(lines):
        is_non_invoice_labeled = any(_line_contains_phrase(line, tokens) for tokens in _NON_INVOICE_DATE_LABEL_TOKENS)

        label_hit: Optional[tuple[str, int, tuple[float, float, float, float], bool]] = None
        for variant in variants:
            tokens = _tokenize(variant)
            after_idx = _find_label_word_span(line, tokens)
            if after_idx is None:
                continue
            label_x0 = line[after_idx - len(tokens)].x0
            remainder = _truncate_at_next_label(line[after_idx:])
            same_line_value = _value_text(remainder)
            if same_line_value and find_date_in_text(same_line_value) is not None:
                label_hit = (same_line_value, line[0].page, line_bbox(remainder), variant == "date")
            elif i + 1 < len(lines):
                next_line = lines[i + 1]
                aligned = sorted((w for w in next_line if w.x0 >= label_x0 - 5), key=lambda w: w.x0)
                value_words = _truncate_at_next_label(_truncate_at_column_gap(aligned))
                next_line_value = _value_text(value_words)
                if next_line_value and find_date_in_text(next_line_value) is not None:
                    label_hit = (next_line_value, next_line[0].page, line_bbox(value_words), variant == "date")
            break  # this line's label is matched (whether or not a value followed) — never retry shorter variants

        if label_hit:
            text, page, bbox, is_bare = label_hit
            base_score = _DATE_BARE_LABEL_SCORE if is_bare else _DATE_LABEL_ANCHOR_SCORE
            candidates.append(Candidate(
                field_name="invoice_date", value=text, method="label_anchor+pattern", page=page, bbox=bbox,
                evidence=_date_candidate_evidence(i, text, is_non_invoice_labeled, base_score),
            ))
            continue

        # No invoice-date label on this line at all — fall back to a plain
        # positional read of whatever date-shaped text the line contains,
        # confidence-tiered exactly like vendor's own positional fallback.
        text = line_text(line).strip()
        if not text or find_date_in_text(text) is None:
            continue
        confidence = _line_confidence(line)
        base_score = (
            _DATE_FLOOR_TIER_SCORE if confidence >= _MIN_POSITIONAL_FALLBACK_CONFIDENCE
            else confidence * _DATE_FLOOR_TIER_SCORE
        )
        candidates.append(Candidate(
            field_name="invoice_date", value=text, method="positional_fallback",
            page=line[0].page, bbox=line_bbox(line),
            evidence=_date_candidate_evidence(i, text, is_non_invoice_labeled, base_score),
        ))

    return candidates


def find_invoice_date_candidate_selection(lines: list[list[PositionedWord]]) -> SelectionResult:
    """The full P1-C candidate/evidence/selection pipeline for
    invoice_date: generate every date-shaped candidate in the document
    (_invoice_date_candidates), then hand them to the same shared
    select_candidate (candidates.py) vendor already uses — no second,
    competing selection mechanism.
    """
    return select_candidate(_invoice_date_candidates(lines), ambiguity_margin=_DATE_AMBIGUITY_MARGIN)


def date_candidate_cleared_confidence_floor(candidate: Candidate) -> bool:
    """True when an invoice_date candidate's own evidence is trustworthy on
    its own terms, independent of whether it beat a rival — the same
    "did this beat its rivals vs. is this trustworthy in absolute terms"
    distinction vendor_candidate_cleared_confidence_floor draws for vendor.
    Any label-anchored candidate (bare "date" included — it is still real
    label evidence, just the weakest form) counts as cleared; a positional
    candidate only clears it once its own OCR confidence does."""
    if candidate.method != "positional_fallback":
        return True
    return candidate.evidence.get("date_evidence", 0.0) >= _DATE_FLOOR_TIER_SCORE


#: A genuine thousands-separator comma always has exactly 3 digits after
#: it ("6,000"); a decimal point always has 1-2 digits after it ("2200.00").
#: Deliberately not "contains a comma" or "contains a period" — found live
#: on a real receipt: a street address ("Plaza 229, Third floor... Phase
#: 7,") has plain punctuation commas with nothing but the next word after
#: them, which the looser check counted as two extra "amount" candidates
#: and made a real, single, unambiguous total look ambiguous (3 candidates
#: instead of 1), so find_total_anywhere correctly-but-uselessly refused to
#: guess among them.
_SPECIFIC_AMOUNT_PATTERN = re.compile(
    r"(?:rs\.?|pkr|\$|usd)\s*-?\d[\d,]*(?:\.\d{1,2})?"
    r"|-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|-?\d+\.\d{1,2}",
    re.IGNORECASE,
)


#: A leading currency word, stripped before the letters check below so
#: "Rs2200" isn't mistaken for a number merged with a word.
_LEADING_CURRENCY = re.compile(r"^\s*(?:rs\.?|pkr|usd|\$)\s*", re.IGNORECASE)


def _looks_like_a_specific_amount(word_text: str) -> bool:
    """True only for a word shaped like someone actually wrote a specific
    money amount, not just any digit run parse_money's own permissive regex
    would accept: it must carry a genuine thousands-grouping comma, a
    decimal point followed by digits, or a currency prefix (Rs/PKR/$/USD,
    glued or spaced). Found live why this matters: a bare digit run with
    none of those — a CNIC fragment ("37405"), an NTN ("0819531-5"), a
    phone number, a bare item quantity — would otherwise outrank the real
    total by raw size. A real total written on a real receipt essentially
    always carries at least one of these markers ("6,000", "2200.00",
    "Rs2200"); an ID number never does, and neither does an address's own
    punctuation comma (see _SPECIFIC_AMOUNT_PATTERN's own note).

    Letters still attached to the digits (after any leading currency word)
    disqualify the token outright, even though the pattern above would
    happily match a prefix of it. Found live, and it is the worst failure
    shape this whole fallback can have: a handwritten "Rs. 500" on a real
    cash-receipt voucher OCR'd as the single token "Rs.5_cleaning" — the
    amount merged with the next printed word and *truncated*. Matching the
    "Rs.5" prefix of that yielded a confidently-reported total of 5.0 for a
    receipt actually worth 500, and worse, moved the document from
    high-priority review down to ordinary review — a wrong number that
    looks more trustworthy than no number. A token whose digits run into
    letters is evidence OCR merged words, so the number cannot be trusted
    to be complete; NOT_FOUND is the correct, safe answer there.
    """
    cleaned = word_text.strip()
    if not _SPECIFIC_AMOUNT_PATTERN.search(cleaned):
        return False
    return not any(c.isalpha() for c in _LEADING_CURRENCY.sub("", cleaned))


def find_total_anywhere(
    lines: list[list[PositionedWord]],
) -> Optional[tuple[str, int, tuple[float, float, float, float]]]:
    """Positional/pattern fallback for `total`, used only when
    find_label_anchored_text finds no "Total:"-style label at all. Found
    live on real Pakistani petty-cash vouchers: a self-issued cash-receipt
    voucher states its amount in a plain prose sentence ("received amount
    Rs. 6,000/- as Salary/ Stipend") with no "Total:" label anywhere on the
    document — a label-anchored search can never find this.

    Deliberately the most conservative of this module's three fallbacks,
    because a wrong total is a real financial-record error in a way a wrong
    vendor guess or a wrong date is not: candidates are restricted to
    words that look like a specifically-written amount
    (_looks_like_a_specific_amount) at high enough OCR confidence, and this
    only ever returns a value when **exactly one** such candidate exists on
    the whole document. Two or more candidates is inherently ambiguous
    (which one is the total vs. a line item, a subtotal, a discount?) and
    per this pipeline's "no lost information over a wrong guess" principle
    (docs/invoice-ocr-plan.md §7), ambiguity means NOT_FOUND, not a guess at
    "the largest one."
    """
    candidates: list[tuple[str, int, tuple[float, float, float, float]]] = []
    for line in lines:
        for word in line:
            if word.confidence < _MIN_POSITIONAL_FALLBACK_CONFIDENCE:
                continue
            if not _looks_like_a_specific_amount(word.text):
                continue
            if parse_money(word.text) is None:
                continue
            candidates.append((word.text, word.page, (word.x0, word.y0, word.x1, word.y1)))

    return candidates[0] if len(candidates) == 1 else None


#: P1-D: how far a winning total candidate must beat its nearest rival to
#: count as a confident ACCEPT rather than REVIEW — the same design as
#: vendor's/invoice_date's own ambiguity margins, sized identically (far
#: smaller than the gap between any two of this field's own tiers).
_TOTAL_AMBIGUITY_MARGIN = 0.05

#: Four label-anchored tiers, ordered by how authoritative the label itself
#: is — not by document order, which is the exact "first match wins" flaw
#: this phase replaces (found live: LABEL_VARIANTS["total"]'s own bare
#: "total" variant matches the *second word* of "Sub Total:"/"Total Tax:"/
#: "Total Discount:", so the old single-shot lookup could return a
#: subtotal, a tax line, or a discount line as if it were the grand total
#: — confirmed via direct reproduction, not assumed). An explicit,
#: unambiguous total label ("Grand Total", "Total Amount", "Net Payable"...)
#: is the strongest possible evidence; the bare "Total" is real label
#: evidence too, but the weakest unambiguous form of it (every totals-
#: adjacent field on a template could technically use the word "total"
#: somewhere); "Amount Due"/"Balance Due" (Phase 7's own explicit family)
#: are weaker still — structurally these can legitimately mean either "the
#: full transaction amount" or "what's left after a partial payment," and
#: only independent evidence (arithmetic consistency, or simply being the
#: only candidate at all) should let one win outright. Gaps (5.0) are
#: deliberately far larger than any single penalty/bonus below (at most
#: ±3.0), so no combination of them can ever let a lower tier outrank a
#: higher one — only decide a tie within a tier, or how a lone candidate's
#: own confidence is ultimately reported.
_TOTAL_STRONG_LABEL_SCORE = 20.0
_TOTAL_BARE_LABEL_SCORE = 15.0
_TOTAL_CONTEXTUAL_LABEL_SCORE = 10.0
#: The dynamic-fields-discovery fallback (extract_invoice.py's own
#: `_dynamic_total` — a real label:value pair the generic discovery pass
#: found under a label spelling not in LABEL_VARIANTS at all, e.g. a glued
#: "BillTotal:") and the pre-existing ultra-conservative unlabeled
#: positional match (find_total_anywhere, reused unmodified below) sit
#: below every genuine total-type label — real evidence, but weaker than
#: any curated, known-good label wording.
_TOTAL_DYNAMIC_LABEL_SCORE = 8.0
_TOTAL_POSITIONAL_SCORE = 5.0

#: Tiny document-order tie-breaker, sized like vendor's/invoice_date's own.
_TOTAL_ORDER_EPSILON = 0.001

#: A monetary value labeled with a *different* financial field's own name
#: (NON_TOTAL_FIELD_LABEL_PHRASES — "Sub Total", "Tax", "Discount", "Cash",
#: "Change", ...) is never the transaction total — the same rejection
#: scale vendor's own reference-number rejection and invoice_date's own
#: non-invoice-date-label rejection use. Can still win a tie-break among
#: other equally-disqualified candidates (the "return something rather
#: than nothing" guarantee), but never outranks a real total candidate.
_TOTAL_NON_TOTAL_FIELD_PENALTY = -1000.0

#: A candidate sitting inside the already-detected line-item table body
#: (extract_invoice.py's own `item_body` range, reused here rather than
#: re-detected) is a per-item figure, never the document's own aggregate
#: total, printed by definition *after* the table ends. Same scale as the
#: penalty above for the same reason: structurally certain, not merely
#: suspicious.
_TOTAL_LINE_ITEM_BODY_PENALTY = -1000.0

#: A candidate whose own value reconciles with the document's internal
#: arithmetic reference (arithmetic.total_reference_amount — subtotal (or
#: line-item sum) + tax − discount) is genuinely strong evidence per Phase
#: 5/D's own instruction — strong enough to decisively win a tie within a
#: tier, or between adjacent tiers whose gap this doesn't fully close
#: (10.0 vs 15.0 needs +5.0; this bonus alone never crosses that), but
#: deliberately still short of a full tier gap on its own, so evidence
#: never has to fight architecture-level tier guarantees to be believed.
_TOTAL_ARITHMETIC_BONUS = 3.0

#: How closely a candidate's value must match the arithmetic reference to
#: count as "confirmed," not merely "roughly plausible" — deliberately
#: tighter than P0-B's own is_plausible_total_magnitude ratio ([0.2, 5.0],
#: built to catch gross corruption, not to discriminate between several
#: plausible-looking candidates). This is a *selection*-time signal, never
#: a substitute for that post-selection safety check, which runs unchanged
#: regardless of what wins here.
_TOTAL_ARITHMETIC_ABS_TOLERANCE = 1.0
_TOTAL_ARITHMETIC_REL_TOLERANCE = 0.01

_TOTAL_CONTEXTUAL_LABEL_PHRASES = ("amount due", "balance due")
_NON_TOTAL_FIELD_LABEL_TOKENS: list[list[str]] = [_tokenize(p) for p in NON_TOTAL_FIELD_LABEL_PHRASES]


def _is_arithmetically_consistent(value: float, reference: Optional[float]) -> bool:
    if reference is None:
        return False
    return abs(value - reference) <= max(_TOTAL_ARITHMETIC_ABS_TOLERANCE, abs(reference) * _TOTAL_ARITHMETIC_REL_TOLERANCE)


def _total_candidate_evidence(
    index: int, amount: Optional[float], is_non_total_labeled: bool, is_in_item_body: bool,
    base_score: float, reference: Optional[float],
) -> dict[str, float]:
    """Every reason a single monetary value might or might not be the
    transaction total, as named, signed contributions — mirrors vendor's
    and invoice_date's own per-candidate evidence functions in spirit.
    `base_score` already encodes which of the four tiers (see above) this
    candidate belongs to; every signal here is layered on top of that
    tier, never used to move a candidate between tiers on its own."""
    evidence: dict[str, float] = {
        "total_evidence": base_score,
        "document_order": -index * _TOTAL_ORDER_EPSILON,
    }
    if is_non_total_labeled:
        evidence["non_total_field_label"] = _TOTAL_NON_TOTAL_FIELD_PENALTY
    if is_in_item_body:
        evidence["line_item_body"] = _TOTAL_LINE_ITEM_BODY_PENALTY
    if amount is not None and _is_arithmetically_consistent(amount, reference):
        # Named distinctly from P0-B's own "arithmetic_consistent" evidence
        # key (extract_invoice.py's post-*selection* check of whether
        # subtotal+tax-discount equals the *final chosen* total, a bool) —
        # this is a *pre*-selection signal about one candidate among
        # several, a float weight. Both coexist unmodified on the final
        # FieldValue; neither is ever overwritten by the other.
        evidence["candidate_reconciles_with_reference"] = _TOTAL_ARITHMETIC_BONUS
    return evidence


def find_total_candidates(
    lines: list[list[PositionedWord]], skip_lines: Optional[set[int]] = None,
    subtotal: Optional[float] = None, tax: Optional[float] = None, discount: Optional[float] = None,
    line_items_amount_sum: Optional[float] = None, item_body: Optional[range] = None,
) -> list[Candidate]:
    """P1-D: generates every total candidate this module's own text-
    scanning can find — every label-anchored line matching
    LABEL_VARIANTS["total"] anywhere in the document (not just the first,
    the exact "first match wins" flaw this phase replaces — see the tier
    constants' own docstring), plus the pre-existing ultra-conservative
    unlabeled positional match (find_total_anywhere, reused unchanged).

    Does NOT include extract_invoice.py's own dynamic-fields-discovery
    fallback (`_dynamic_total`) — that consults `discovered`, a cross-
    field artifact this module has no reason to know about; the caller
    (extract_invoice.py's `_total_field`) builds that one candidate itself
    and merges it into this list before calling `select_candidate`, so
    every total candidate — regardless of which tier or which module
    produced it — is judged by the exact same shared selection mechanism.

    Per-line label matching deliberately mirrors find_label_anchored_text's
    own same-line/next-line value lookup (reusing its exact helpers) rather
    than reimplementing it differently, so this generalizes the same way
    that function already does (packed multi-column rows, right-aligned
    totals values) instead of regressing on it.
    """
    reference = total_reference_amount(subtotal, tax, discount, line_items_amount_sum)
    variants = sorted(LABEL_VARIANTS["total"], key=lambda v: len(v.split()), reverse=True)
    candidates: list[Candidate] = []

    for i, line in enumerate(lines):
        if skip_lines and i in skip_lines:
            continue
        is_in_item_body = item_body is not None and i in item_body

        label_hit: Optional[tuple[str, float, int, tuple[float, float, float, float], str]] = None
        for variant in variants:
            tokens = _tokenize(variant)
            after_idx = _find_label_word_span(line, tokens)
            if after_idx is None:
                continue
            label_x0 = line[after_idx - len(tokens)].x0
            remainder = _truncate_at_next_label(line[after_idx:])
            same_line_value = _value_text(remainder)
            amount = parse_money(same_line_value) if same_line_value else None
            if amount is not None:
                label_hit = (same_line_value, amount, line[0].page, line_bbox(remainder), variant)
            elif i + 1 < len(lines):
                next_line = lines[i + 1]
                aligned = sorted((w for w in next_line if w.x0 >= label_x0 - 5), key=lambda w: w.x0)
                value_words = _truncate_at_next_label(_truncate_at_column_gap(aligned))
                next_line_value = _value_text(value_words)
                amount = parse_money(next_line_value) if next_line_value else None
                if amount is not None:
                    label_hit = (next_line_value, amount, next_line[0].page, line_bbox(value_words), variant)
            break  # this line's label is matched — never retry shorter variants

        if not label_hit:
            continue
        text, amount, page, bbox, variant = label_hit
        if variant in _TOTAL_CONTEXTUAL_LABEL_PHRASES:
            base_score = _TOTAL_CONTEXTUAL_LABEL_SCORE
        elif variant == "total":
            base_score = _TOTAL_BARE_LABEL_SCORE
        else:
            base_score = _TOTAL_STRONG_LABEL_SCORE
        # The non-total-field-label check only applies when the *bare*
        # "total" token is what matched — found live: "Amount Inc. Sales
        # Tax" (a genuine, specific, strong total label) itself contains
        # the word "tax", which would otherwise self-penalize the very
        # label that makes this candidate legitimate. A specific,
        # unambiguous total phrase (grand total, net payable, amount inc
        # sales tax, ...) is never at risk of actually being a different
        # field's own label the way the bare, generic "total" token is —
        # that promiscuity is exactly what NON_TOTAL_FIELD_LABEL_PHRASES
        # exists to catch (a "Sub Total"/"Total Tax"/"Total Discount" line
        # matching only via that bare token).
        is_non_total_labeled = variant == "total" and any(
            _line_contains_phrase(line, tokens) for tokens in _NON_TOTAL_FIELD_LABEL_TOKENS
        )
        candidates.append(Candidate(
            field_name="total", value=text, method="label_anchor+pattern", page=page, bbox=bbox,
            evidence=_total_candidate_evidence(i, amount, is_non_total_labeled, is_in_item_body, base_score, reference),
        ))

    positional = find_total_anywhere(lines)
    if positional is not None:
        text, page, bbox = positional
        amount = parse_money(text)
        if amount is not None:
            candidates.append(Candidate(
                field_name="total", value=text, method="positional_fallback", page=page, bbox=bbox,
                evidence=_total_candidate_evidence(
                    len(lines), amount, False, False, _TOTAL_POSITIONAL_SCORE, reference,
                ),
            ))

    return candidates


def find_total_candidate_selection(
    lines: list[list[PositionedWord]], skip_lines: Optional[set[int]] = None,
    subtotal: Optional[float] = None, tax: Optional[float] = None, discount: Optional[float] = None,
    line_items_amount_sum: Optional[float] = None, item_body: Optional[range] = None,
    extra_candidates: Optional[list[Candidate]] = None,
) -> SelectionResult:
    """The full P1-D candidate/evidence/selection pipeline for total:
    every candidate this module's own text-scanning finds
    (find_total_candidates) plus any the caller supplies (extract_invoice.
    py's own dynamic-fields-discovery candidate — see find_total_candidates'
    own docstring for why that one is built elsewhere), all judged by the
    exact same shared select_candidate (candidates.py) vendor and
    invoice_date already use — no second, competing selection mechanism,
    and no candidate — regardless of tier or origin — ever bypasses it."""
    candidates = find_total_candidates(
        lines, skip_lines, subtotal=subtotal, tax=tax, discount=discount,
        line_items_amount_sum=line_items_amount_sum, item_body=item_body,
    )
    if extra_candidates:
        candidates = candidates + extra_candidates
    return select_candidate(candidates, ambiguity_margin=_TOTAL_AMBIGUITY_MARGIN)


def total_candidate_cleared_confidence_floor(candidate: Candidate) -> bool:
    """True when a total candidate's own evidence is trustworthy on its
    own terms, independent of whether it beat a rival — the same
    distinction vendor_candidate_cleared_confidence_floor and
    date_candidate_cleared_confidence_floor draw for their own fields.

    A strong or bare total-specific label always clears it. A weaker tier
    (Amount Due/Balance Due, the dynamic-discovery fallback, or an
    unlabeled positional match) clears it only when independently
    confirmed by arithmetic consistency — exactly Phase 7's own
    instruction: "Amount Due: 1000" alone, with nothing to reconcile
    against, is a real, honest maybe, not a confident certainty; "Amount
    Due: 1000" that also reconciles with subtotal+tax-discount is no
    longer just a label guess."""
    if candidate.evidence.get("total_evidence", 0.0) >= _TOTAL_BARE_LABEL_SCORE:
        return True
    return "candidate_reconciles_with_reference" in candidate.evidence


def dynamic_total_candidate_evidence(amount: float, reference: Optional[float]) -> dict[str, float]:
    """Evidence for extract_invoice.py's own dynamic-fields-discovery total
    candidate (`_dynamic_total_candidate` — a real label:value pair the
    generic discovery pass found under a label spelling not in
    LABEL_VARIANTS at all, e.g. a glued "BillTotal:"). Exposed here rather
    than duplicated in extract_invoice.py so the dynamic tier's own score
    and arithmetic-consistency check stay defined in exactly one place,
    alongside every other total tier's own scoring."""
    evidence: dict[str, float] = {"total_evidence": _TOTAL_DYNAMIC_LABEL_SCORE, "document_order": 0.0}
    if _is_arithmetically_consistent(amount, reference):
        evidence["candidate_reconciles_with_reference"] = _TOTAL_ARITHMETIC_BONUS
    return evidence
