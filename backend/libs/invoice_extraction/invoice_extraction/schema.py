"""Output structure — Rule 8.2/8.3. Every field carries its value alongside
enough metadata to audit and review it, not just the value alone; this is
what makes the "Needs Review" UX in §2a possible (show exactly which
field(s) are uncertain and why, not a blanket "please recheck everything").

Rule 8.1 ("separate raw extraction from validated/final data, never
overwrite the raw extraction") is a data-*storage* concern for whichever
service persists this — this library always returns the raw extraction; the
corrected/edited copy after human review lives in that service's own DB
layer (Phase 3, Invoice Service), not here.
"""
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Literal, Optional

from invoice_extraction.dynamic_fields import DiscoveredField


@dataclass
class FieldValue:
    """One extracted field. `value` is None when nothing could be located
    at all — that is itself meaningful (routes to needs_review, §6.4), so
    it is never silently coerced to an empty string or zero."""

    value: Optional[object]
    confidence: float
    #: Which extraction rule produced this — Rule 6.3's confidence-tiering
    #: input. One of "label_anchor+pattern", "label_anchor",
    #: "positional_fallback", or "not_found".
    method: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    #: P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md) —
    #: named plausibility signals behind this field's own confidence, e.g.
    #: `total`'s {"arithmetic_consistent", "within_document_magnitude",
    #: "shares_source_with_another_field"}. None for every field this
    #: audit's P0 phase didn't touch, so no existing caller/test is
    #: affected — this is additive, not a parallel structure replacing
    #: confidence/method above.
    evidence: Optional[dict[str, object]] = None


@dataclass
class LineItem:
    description: FieldValue
    qty: FieldValue
    rate: FieldValue
    amount: FieldValue
    #: Rule 5.1 — "pass"/"fail" once all three of qty/rate/amount are
    #: present; "not_checked" if any is missing (nothing to cross-validate).
    arithmetic_check: Literal["pass", "fail", "not_checked"] = "not_checked"
    #: Rule 4.4's documented failure modes, when detected — e.g.
    #: "qty_rate_amount_mismatch", "sparse_row_possible_merged_cell".
    review_flags: list[str] = field(default_factory=list)


def not_found(method: str = "not_found") -> FieldValue:
    return FieldValue(value=None, confidence=0.0, method=method)


@dataclass
class ArithmeticValidation:
    #: Fraction of line items that passed Rule 5.1 (None if there were none
    #: to check), not just a bool — one bad row among twenty good ones is a
    #: different confidence signal than every row failing.
    line_items_pass_rate: Optional[float]
    totals_pass: Optional[bool]  # Rule 5.2; None if subtotal/total weren't both found
    tax_rate_plausible: Optional[bool]  # Rule 5.3; None if tax/subtotal weren't both found
    date_plausible: Optional[bool]  # Rule 5.4; None if no date was found
    #: P0-B; None if total had no document-internal reference amount
    #: (neither a subtotal nor any line items) to be checked against at all.
    magnitude_plausible: Optional[bool] = None


@dataclass
class ExtractedInvoice:
    vendor_name: FieldValue
    invoice_number: FieldValue
    invoice_date: FieldValue
    ntn: FieldValue
    subtotal: FieldValue
    tax_rate: FieldValue
    tax_amount: FieldValue
    total: FieldValue
    line_items: list[LineItem]
    arithmetic_validation: ArithmeticValidation
    #: Rule 6.1 — the composite document-level score driving routing below.
    document_confidence: float
    #: "pdf_text" | "ocr" — Rule 6.2's base-confidence dimension; carried
    #: through from ocr.ExtractionResult.method.
    extraction_source: Literal["pdf_text", "ocr"]
    review_status: Literal["auto_processed", "needs_review", "needs_review_high_priority"]
    #: Document-level flags (as opposed to LineItem.review_flags) — e.g.
    #: "no_total_found", "arithmetic_mismatch", "tax_rate_implausible".
    review_flags: list[str] = field(default_factory=list)
    #: Carried straight through from ocr.ExtractionResult.page_dimensions —
    #: lets a caller scale a field's bbox onto a rendered page image (see
    #: that field's own docstring for the native-units explanation).
    page_dimensions: list[tuple[float, float]] = field(default_factory=list)
    #: Label-anchored only ("Bill To"/"To"/"Customer") — most real documents
    #: (a walk-in retail receipt especially) never name a customer at all,
    #: so this is legitimately absent (not_found) far more often than
    #: vendor_name; no positional fallback exists for it, unlike vendor,
    #: since there's no reliable "customer is conventionally here" position
    #: convention the way there is for a vendor name at the top of a page.
    customer_name: FieldValue = field(default_factory=not_found)
    #: ISO code ("PKR", "USD", ...) from a currency symbol/word actually
    #: found on the document — see currency.py. Never defaults; not_found
    #: means genuinely no currency evidence, not "assume USD".
    currency: FieldValue = field(default_factory=not_found)
    #: "invoice" | "receipt" | "bill" | "quotation" | "credit_note" |
    #: "purchase_order" | "minute_sheet" | "approval_request" | None — see
    #: document_type.py. Determined from document text evidence, never from
    #: filename/extension.
    document_type: FieldValue = field(default_factory=not_found)
    #: The document's own wording that decided `document_type`, quoted
    #: verbatim ("For approval of Rs. 22,875/-") — shown to a reviewer so
    #: the classification is explainable rather than an opaque label.
    classification_reason: Optional[str] = None
    #: Whether this document should enter financial transaction processing
    #: at all — derived from document_type (document_type.is_transactional),
    #: not classified separately. False for internal paperwork like a
    #: minute sheet or an approval request.
    transactional: bool = True
    #: An amount the document *states* when it is not a financial
    #: transaction ("For approval of Rs. 22,875/-"). Deliberately a separate
    #: field from `total`, never a substitute for it: a non-transactional
    #: document has no transaction total, and letting its stated amount
    #: occupy `total` is exactly how an internal approval memo silently
    #: becomes a booked expense. Displayed as "Amount mentioned"; only
    #: becomes a total if a human reclassifies the document as
    #: transactional.
    amount_mentioned: Optional[float] = None
    #: "PAID" | "UNPAID" | "PARTIALLY_PAID" | "DUE" | "PENDING" | None — see
    #: payment.py. Only set from direct textual evidence; "not detected"
    #: stays not_found, never silently becomes PENDING.
    payment_status: FieldValue = field(default_factory=not_found)
    #: City name found in the document text — see geography.py.
    city: FieldValue = field(default_factory=not_found)
    #: Country inferred only from a matched city or phone country code —
    #: see geography.py. Never defaults to any country.
    country: FieldValue = field(default_factory=not_found)
    #: Every other labelled field found anywhere on the document — see
    #: dynamic_fields.py. This is what stops a vendor-specific field the
    #: canonical extractors have no name for ("Shipping & Handling", "PO
    #: #", "Payment Terms") from being read by OCR and then silently
    #: dropped. Entries duplicating an already-populated canonical field
    #: are filtered out, so this is additional information, not a restated
    #: copy of the fields above.
    dynamic_fields: list["DiscoveredField"] = field(default_factory=list)
    #: P1-E.1 (docs/invoice-ocr-plan.md §25): the document's own full raw
    #: OCR text, carried straight through from ocr.ExtractionResult.text —
    #: the same text document_type/currency/city/country/payment_status
    #: already scan internally, now also exposed on the output itself.
    #: Added specifically so a downstream consumer (invoice-service's
    #: category_classifier.py) can use genuine prose evidence a document
    #: states in free text but never as a structured field — a real,
    #: confirmed gap: a cash-receipt voucher's own "received amount Rs.
    #: 6,000/- as Salary/ Stipend" is real category evidence that exists on
    #: the document, but was invisible to any consumer that only ever saw
    #: vendor_name/line_items, since prose sentences never populate either.
    #: None only when there is no OCR text at all to carry (never a
    #: distinct signal in its own right — a caller with no other use for it
    #: can simply ignore this field).
    raw_text: Optional[str] = None
