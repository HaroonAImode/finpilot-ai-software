"""Document type detection — never assumes every upload is an invoice, and
never determines type from filename/extension. Deterministic keyword
evidence over the document's own OCR text; when no keyword is present, only
a weak, explicitly-lower-confidence "receipt" guess is offered for a
line-item-table-shaped document, never asserted as fact.

Type is also what decides whether a document enters financial transaction
processing at all (see NON_TRANSACTIONAL_TYPES) — deliberately derived from
this one classifier rather than classified a second, independent time, so
there is exactly one place a document's identity is decided and exactly one
place to correct when it is wrong.
"""
import re
from typing import Literal, NamedTuple, Optional

DocumentType = Literal[
    "invoice", "receipt", "bill", "quotation", "credit_note", "purchase_order",
    # Internal company paperwork that carries amounts but is not itself a
    # purchase/sale — see NON_TRANSACTIONAL_TYPES below.
    "minute_sheet", "approval_request",
    "unknown",
]

#: Internal company paperwork with no real external counterparty — its
#: "vendor-shaped" text is the issuing company's own letterhead, and its
#: only usable amount is a prose mention with no formal total field at all.
#: Every financial field (total, subtotal, tax, discount, vendor_name) is
#: force-cleared for these in extract_invoice.py; the prose amount survives
#: separately as `amount_mentioned`. Found live on a real 35-document set: 4
#: of them were internal expense-approval memos being pushed through
#: purchase-invoice extraction.
_INTERNAL_NON_TRANSACTIONAL_TYPES: frozenset[str] = frozenset({"minute_sheet", "approval_request"})

#: P0-A (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md's P0-1
#: finding): a quotation or purchase order is a real *external* commercial
#: document — it names a real vendor/supplier and carries a real quoted/
#: ordered total that describes what the document itself actually says —
#: but neither one represents money that has actually changed hands yet, so
#: neither may contribute to the cashbook (expense totals, cash/online
#: totals, transaction counts). Unlike _INTERNAL_NON_TRANSACTIONAL_TYPES
#: above, extract_invoice.py does NOT clear these documents' vendor_name or
#: financial fields — a quotation's own quoted total is meaningful
#: information about the quotation, not a fabricated value, and destroying
#: it would contradict the "never invent, but never discard real evidence
#: either" principle this whole pipeline already follows elsewhere. Keeping
#: them out of financial totals is achieved entirely through
#: `transactional=False` (see is_transactional below) — the same mechanism
#: invoice-service's `transaction_status` filtering already uses to exclude
#: every non-transactional type from every cashbook query, regardless of
#: whether that type's own fields are populated or cleared.
EXTERNAL_NON_TRANSACTIONAL_TYPES: frozenset[str] = frozenset({"quotation", "purchase_order"})

#: Document types that are *not* financial transactions — the union of both
#: sets above. `is_transactional()` treats every member identically for
#: routing purposes (never `auto_processed`, see extract_invoice.py); the
#: two sets differ only in whether their own fields get cleared, not in
#: whether they're excluded from the cashbook.
NON_TRANSACTIONAL_TYPES: frozenset[str] = _INTERNAL_NON_TRANSACTIONAL_TYPES | EXTERNAL_NON_TRANSACTIONAL_TYPES

# Checked in this order (first match wins) — more specific multi-word terms
# before the single generic ones they could otherwise be confused with.
#
# The two internal-paperwork types lead deliberately, ahead of the generic
# "invoice"/"receipt"/"bill" words. Found live, and it is exactly the bug
# this ordering exists to prevent: an internal "Minute Sheet" expense memo
# itemises its attachments as "Bill-1(Legal)", "Bill-2(Emp Care)", … so the
# generic `\bbill\b` matched and typed all four such documents as "bill"
# with confidence 1.0 — confidently wrong, not merely unknown. Their own
# title ("MINUTE SHEET") and approval wording are far stronger evidence of
# what the document actually is than a word appearing in its line items.
_TYPE_KEYWORDS: list[tuple[str, DocumentType]] = [
    (r"\bminute\s*sheet\b", "minute_sheet"),
    # Approval wording, as whole phrases rather than the bare word
    # "approval": a normal invoice can legitimately say "approved" in a
    # payment-terms footer, so only phrasings that frame the document
    # itself as a request for approval count.
    (r"\bfor\s+approval\s+of\b", "approval_request"),
    (r"\bsubmitted\s+for\s+approval\b", "approval_request"),
    (r"\bkindly\s+approve\b", "approval_request"),
    (r"\brequest\s+for\s+approval\b", "approval_request"),
    (r"\bcredit\s*note\b", "credit_note"),
    (r"\bdebit\s*note\b", "credit_note"),
    (r"\bpurchase\s*order\b", "purchase_order"),
    (r"\bquotation\b", "quotation"),
    (r"\binvoice\b", "invoice"),
    (r"\breceipt\b", "receipt"),
    (r"\bbill\b", "bill"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), doc_type) for pattern, doc_type in _TYPE_KEYWORDS]


class DocumentTypeResult(NamedTuple):
    """What was detected, how strongly, and the evidence for it.

    `reason` carries the document's own matched wording rather than a rule
    id — the review UI shows it verbatim ("Detected approval wording: 'For
    approval of Rs. 22,875/-'") so a person can see why the system decided
    what it did and judge the call themselves, instead of being asked to
    trust an opaque label.
    """

    document_type: Optional[DocumentType]
    confidence: float
    reason: Optional[str]


def _matched_phrase(text: str, match: re.Match) -> str:
    """The matched wording plus a little of what follows it, so the reason
    shown to a person is the document's real sentence ("For approval of Rs.
    22,875/-") rather than the bare keyword that triggered it."""
    start = match.start()
    snippet = " ".join(text[start:start + 60].split())
    return snippet.strip()


def detect_document_type(text: str, has_line_items: bool, has_total: bool) -> DocumentTypeResult:
    """Returns (type, confidence, reason). A type-naming keyword actually
    present on the document is the strongest signal (confidence 1.0, same
    tier as a deterministic pattern match elsewhere). With no keyword at all
    but a recognizable line-item table plus a total, "receipt" is offered as
    a weak, explicitly low-confidence guess for a retail-shaped document —
    still distinguishable downstream from a confident classification, never
    silently equal to one. With neither, the honest answer is unknown."""
    for pattern, doc_type in _COMPILED:
        match = pattern.search(text)
        if match:
            return DocumentTypeResult(doc_type, 1.0, _matched_phrase(text, match))
    if has_line_items and has_total:
        return DocumentTypeResult("receipt", 0.4, "No document-type wording found; has a line-item table and a total")
    return DocumentTypeResult(None, 0.0, None)


#: The amount an approval document states as the thing being approved
#: ("For approval of Rs. 22,875/- (Twenty two thousand…) by CEO"). Anchored
#: on the approval phrasing itself rather than scanning the page for
#: amounts: these documents list every attached bill's amount too, and the
#: figure that matters is specifically the one the sentence is about.
#:
#: This is deliberately *not* wired into total extraction. The same wording
#: was examined for that earlier and rejected — reading it as a total is
#: exactly how an internal memo becomes a booked expense. It is safe here
#: only because it can populate `amount_mentioned` on a document already
#: classified non-transactional, and nothing downstream treats that as money
#: owed.
_APPROVAL_AMOUNT = re.compile(
    r"\b(?:for\s+approval\s+of|submitted\s+for\s+approval\s+of|kindly\s+approve)\s*"
    r"(?:rs\.?|pkr|/-)?\s*(-?[\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


def find_approval_amount_text(text: str) -> Optional[str]:
    """The amount an approval document states as the subject of its
    request, as printed text (parse it with validators.parse_money).
    None when the document states no such amount."""
    match = _APPROVAL_AMOUNT.search(text)
    return match.group(1) if match else None


def is_transactional(document_type: Optional[str]) -> bool:
    """Whether a document of this type should enter financial transaction
    processing. An unknown/undetected type is treated as transactional:
    this pipeline's whole purpose is processing financial documents, so the
    safe default for "we could not tell" is the normal path plus the review
    the confidence system already routes it to — not silently diverting a
    real invoice into a non-transactional bucket a person may never look at.
    """
    return document_type not in NON_TRANSACTIONAL_TYPES
