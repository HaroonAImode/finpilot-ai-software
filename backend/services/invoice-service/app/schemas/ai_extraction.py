"""Mirrors AI Engine's ExtractedInvoiceResponse (ai-engine/app/schemas/ocr.py)
— the JSON shape this service receives from POST /api/v1/ai/ocr/extract.
Kept as this service's own copy rather than a shared import so the two
services can evolve independently (the same reasoning ai-engine's own
schemas.py gives for not returning its internal dataclasses directly).
"""
from typing import Literal, Optional

from pydantic import BaseModel


class ExtractedFieldSchema(BaseModel):
    value: Optional[object]
    confidence: float
    method: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    #: FOUND / NOT_FOUND / UNCERTAIN — optional with a default so existing
    #: test fixtures constructing this schema directly (without going
    #: through the real ai-engine response) don't need updating; a real
    #: ai-engine response always sets it.
    status: Optional[Literal["FOUND", "NOT_FOUND", "UNCERTAIN"]] = None
    #: P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md) —
    #: named plausibility signals mirrored straight from AI Engine's
    #: FieldValueResponse.evidence. None for every field this audit's P0
    #: phase didn't touch, and for any fixture predating it.
    evidence: Optional[dict[str, object]] = None


class ExtractedLineItemSchema(BaseModel):
    description: ExtractedFieldSchema
    qty: ExtractedFieldSchema
    rate: ExtractedFieldSchema
    amount: ExtractedFieldSchema
    arithmetic_check: Literal["pass", "fail", "not_checked"]
    review_flags: list[str]


class ArithmeticValidationSchema(BaseModel):
    line_items_pass_rate: Optional[float]
    totals_pass: Optional[bool]
    tax_rate_plausible: Optional[bool]
    date_plausible: Optional[bool]
    #: P0-B — None when total had no document-internal reference amount to
    #: be checked against, and for any fixture predating this field.
    magnitude_plausible: Optional[bool] = None


class DiscoveredFieldSchema(BaseModel):
    """Mirrors AI Engine's DiscoveredFieldResponse — one generically
    discovered label/value pair (see invoice_extraction.dynamic_fields)."""

    key: str
    label: str
    value: str
    confidence: float
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None


class ExtractedInvoiceSchema(BaseModel):
    vendor_name: ExtractedFieldSchema
    invoice_number: ExtractedFieldSchema
    invoice_date: ExtractedFieldSchema
    ntn: ExtractedFieldSchema
    subtotal: ExtractedFieldSchema
    tax_rate: ExtractedFieldSchema
    tax_amount: ExtractedFieldSchema
    total: ExtractedFieldSchema
    line_items: list[ExtractedLineItemSchema]
    arithmetic_validation: ArithmeticValidationSchema
    document_confidence: float
    extraction_source: Literal["pdf_text", "ocr"]
    review_status: Literal["auto_processed", "needs_review", "needs_review_high_priority"]
    review_flags: list[str]
    page_dimensions: list[tuple[float, float]] = []
    #: Receipt/bill robustness additions — optional with not_found-shaped
    #: defaults so existing test fixtures don't need updating.
    customer_name: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    currency: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    document_type: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    #: Whether AI Engine judged this a financial transaction document at all.
    #: Defaults True so an extraction produced before this field existed
    #: (and any caller not sending it) reads as transactional, unchanged.
    transactional: bool = True
    #: The document's own wording behind document_type, quoted verbatim.
    classification_reason: Optional[str] = None
    #: An amount a non-transactional document states. Never a total.
    amount_mentioned: Optional[float] = None
    payment_status: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    city: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    country: ExtractedFieldSchema = ExtractedFieldSchema(value=None, confidence=0.0, method="not_found")
    #: Vendor-specific fields with no canonical name here — defaulted to []
    #: so fixtures and any AI Engine build predating this still validate.
    dynamic_fields: list[DiscoveredFieldSchema] = []
    #: Document Preprocessing (docs/invoice-ocr-plan.md §7) — populated only
    #: when AI Engine confidently found more than one separate document in
    #: this upload. Empty for the overwhelming common case and for any AI
    #: Engine build predating this field, so existing scan behavior is
    #: completely unaffected unless this is actually non-empty.
    additional_documents: list["AdditionalDocumentSchema"] = []
    #: P1-E.1 (docs/invoice-ocr-plan.md §25): the document's own full raw
    #: OCR text — mirrors AI Engine's own ExtractedInvoiceResponse.raw_text.
    #: Consumed only by category_classifier.py's own prose-evidence
    #: matching; defaulted to None so a response predating this field, or
    #: any existing test fixture, still validates unchanged.
    raw_text: Optional[str] = None


class AdditionalDocumentSchema(BaseModel):
    """Mirrors AI Engine's AdditionalDocumentResponse — one extra document
    Document Preprocessing split out of the same upload, with its own full
    extraction plus the actual cropped image bytes (base64) so scanner.py
    can store it as that document's own Invoice."""

    extracted: ExtractedInvoiceSchema
    image_base64: str
    filename: str
    mimetype: str


ExtractedInvoiceSchema.model_rebuild()
