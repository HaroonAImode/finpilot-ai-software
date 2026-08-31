"""API response shapes for the OCR/structuring endpoint — a Pydantic mirror
of invoice_extraction.schema's dataclasses, kept as a separate layer (not
the dataclasses returned directly) so the API's response contract can stay
stable even if the library's internal representation changes.
"""
from datetime import date as date_type
from typing import Literal, Optional

from pydantic import BaseModel

from invoice_extraction.confidence import field_status
from invoice_extraction.dynamic_fields import DiscoveredField
from invoice_extraction.schema import ArithmeticValidation, ExtractedInvoice, FieldValue, LineItem


class FieldValueResponse(BaseModel):
    value: Optional[object]
    confidence: float
    method: str
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    #: FOUND / NOT_FOUND / UNCERTAIN — the explicit tri-state a review UI
    #: needs (docs/invoice-ocr-plan.md's receipt-robustness notes), derived
    #: from value presence + confidence rather than requiring the caller to
    #: re-derive it from the two raw numbers every time.
    status: Literal["FOUND", "NOT_FOUND", "UNCERTAIN"]
    #: P0-B (docs/research/DETERMINISTIC_DOCUMENT_INTELLIGENCE_AUDIT.md) —
    #: named plausibility signals mirrored straight from
    #: invoice_extraction.schema.FieldValue.evidence. None for every field
    #: this audit's P0 phase didn't touch.
    evidence: Optional[dict[str, object]] = None

    @classmethod
    def from_field_value(cls, fv: FieldValue) -> "FieldValueResponse":
        value = fv.value.isoformat() if isinstance(fv.value, date_type) else fv.value
        return cls(
            value=value, confidence=fv.confidence, method=fv.method, page=fv.page, bbox=fv.bbox,
            status=field_status(fv.value is not None, fv.confidence), evidence=fv.evidence,
        )


class LineItemResponse(BaseModel):
    description: FieldValueResponse
    qty: FieldValueResponse
    rate: FieldValueResponse
    amount: FieldValueResponse
    arithmetic_check: Literal["pass", "fail", "not_checked"]
    review_flags: list[str]

    @classmethod
    def from_line_item(cls, item: LineItem) -> "LineItemResponse":
        return cls(
            description=FieldValueResponse.from_field_value(item.description),
            qty=FieldValueResponse.from_field_value(item.qty),
            rate=FieldValueResponse.from_field_value(item.rate),
            amount=FieldValueResponse.from_field_value(item.amount),
            arithmetic_check=item.arithmetic_check,
            review_flags=item.review_flags,
        )


class ArithmeticValidationResponse(BaseModel):
    line_items_pass_rate: Optional[float]
    totals_pass: Optional[bool]
    tax_rate_plausible: Optional[bool]
    date_plausible: Optional[bool]
    #: P0-B — None when total had no document-internal reference amount
    #: (neither a subtotal nor any line items) to be checked against.
    magnitude_plausible: Optional[bool] = None

    @classmethod
    def from_validation(cls, v: ArithmeticValidation) -> "ArithmeticValidationResponse":
        return cls(
            line_items_pass_rate=v.line_items_pass_rate, totals_pass=v.totals_pass,
            tax_rate_plausible=v.tax_rate_plausible, date_plausible=v.date_plausible,
            magnitude_plausible=v.magnitude_plausible,
        )


class DiscoveredFieldResponse(BaseModel):
    """One generically-discovered label/value pair — carries the vendor's
    own printed label alongside the normalised key, so a UI can show the
    wording the document actually used rather than this pipeline's
    normalisation of it."""

    key: str
    label: str
    value: str
    confidence: float
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None

    @classmethod
    def from_discovered_field(cls, f: DiscoveredField) -> "DiscoveredFieldResponse":
        return cls(
            key=f.key, label=f.label, value=f.value,
            confidence=f.confidence, page=f.page, bbox=f.bbox,
        )


class ExtractedInvoiceResponse(BaseModel):
    """Mirrors docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md
    Rule 8.3's recommended output structure."""

    vendor_name: FieldValueResponse
    invoice_number: FieldValueResponse
    invoice_date: FieldValueResponse
    ntn: FieldValueResponse
    subtotal: FieldValueResponse
    tax_rate: FieldValueResponse
    tax_amount: FieldValueResponse
    total: FieldValueResponse
    line_items: list[LineItemResponse]
    arithmetic_validation: ArithmeticValidationResponse
    document_confidence: float
    extraction_source: Literal["pdf_text", "ocr"]
    review_status: Literal["auto_processed", "needs_review", "needs_review_high_priority"]
    review_flags: list[str]
    #: (width, height) per page, in the same native units as each field's
    #: bbox — see invoice_extraction.schema.ExtractedInvoice.page_dimensions.
    page_dimensions: list[tuple[float, float]] = []
    #: Receipt/bill robustness additions — all optional, all None/not_found
    #: when there's no evidence (never a guessed default; see each
    #: detector module's own docstring for the specific bug this avoids).
    customer_name: FieldValueResponse
    currency: FieldValueResponse
    document_type: FieldValueResponse
    #: Whether this document should enter financial transaction processing —
    #: derived from document_type (see invoice_extraction.document_type).
    #: Defaults True so a caller built against the previous response shape,
    #: and any document scanned before this existed, reads as transactional
    #: exactly as it did before.
    transactional: bool = True
    #: The document's own wording that decided document_type, quoted
    #: verbatim — shown to a reviewer as the reason for the classification.
    classification_reason: Optional[str] = None
    #: An amount a *non-transactional* document states ("For approval of Rs.
    #: 22,875/-"). Never a transaction total; see ExtractedInvoice's own
    #: field docstring for why the two are kept apart.
    amount_mentioned: Optional[float] = None
    payment_status: FieldValueResponse
    city: FieldValueResponse
    country: FieldValueResponse
    #: Every other labelled field found on the document (see
    #: invoice_extraction.dynamic_fields) — vendor-specific fields this
    #: pipeline has no canonical name for, which would otherwise be read by
    #: OCR and then dropped. Defaulted to [] so a caller built against the
    #: previous response shape keeps validating unchanged.
    dynamic_fields: list[DiscoveredFieldResponse] = []
    #: Document Preprocessing (docs/invoice-ocr-plan.md §7) — populated only
    #: when the upload confidently contained *more than one* separate
    #: document (a phone photo of two receipts side by side, say). Empty
    #: for the overwhelming common case, so this field is purely additive:
    #: a caller that ignores it sees exactly today's single-document
    #: response, unchanged.
    additional_documents: list["AdditionalDocumentResponse"] = []
    #: P1-E.1 (docs/invoice-ocr-plan.md §25): the document's own full raw
    #: OCR text — see invoice_extraction.schema.ExtractedInvoice.raw_text's
    #: own docstring for why this is exposed (category_classifier.py's own
    #: prose-evidence gap). Defaulted to None so a caller built against the
    #: previous response shape is unaffected.
    raw_text: Optional[str] = None

    @classmethod
    def from_extracted_invoice(cls, invoice: ExtractedInvoice) -> "ExtractedInvoiceResponse":
        return cls(
            vendor_name=FieldValueResponse.from_field_value(invoice.vendor_name),
            invoice_number=FieldValueResponse.from_field_value(invoice.invoice_number),
            invoice_date=FieldValueResponse.from_field_value(invoice.invoice_date),
            ntn=FieldValueResponse.from_field_value(invoice.ntn),
            subtotal=FieldValueResponse.from_field_value(invoice.subtotal),
            tax_rate=FieldValueResponse.from_field_value(invoice.tax_rate),
            tax_amount=FieldValueResponse.from_field_value(invoice.tax_amount),
            total=FieldValueResponse.from_field_value(invoice.total),
            line_items=[LineItemResponse.from_line_item(i) for i in invoice.line_items],
            arithmetic_validation=ArithmeticValidationResponse.from_validation(invoice.arithmetic_validation),
            document_confidence=invoice.document_confidence,
            extraction_source=invoice.extraction_source,
            review_status=invoice.review_status,
            review_flags=invoice.review_flags,
            page_dimensions=invoice.page_dimensions,
            customer_name=FieldValueResponse.from_field_value(invoice.customer_name),
            currency=FieldValueResponse.from_field_value(invoice.currency),
            document_type=FieldValueResponse.from_field_value(invoice.document_type),
            transactional=invoice.transactional,
            classification_reason=invoice.classification_reason,
            amount_mentioned=invoice.amount_mentioned,
            payment_status=FieldValueResponse.from_field_value(invoice.payment_status),
            city=FieldValueResponse.from_field_value(invoice.city),
            country=FieldValueResponse.from_field_value(invoice.country),
            dynamic_fields=[DiscoveredFieldResponse.from_discovered_field(f) for f in invoice.dynamic_fields],
            raw_text=invoice.raw_text,
        )


class AdditionalDocumentResponse(BaseModel):
    """One extra document Document Preprocessing split out of the same
    upload (docs/invoice-ocr-plan.md §7) — the same full extraction shape a
    primary result gets, plus the actual cropped image bytes so the caller
    (Invoice Service) can store it as that document's own source file.
    Base64, not raw bytes: this is one small field in an internal JSON
    response, not a bulk file-transfer endpoint — the same data-URI-for-a-
    small-image precedent Settings Service's own Company.logo_url already
    established, not a new pattern invented here.
    """

    extracted: ExtractedInvoiceResponse
    image_base64: str
    filename: str
    mimetype: str


# Resolves the forward reference in ExtractedInvoiceResponse.additional_documents
# above — required because the two classes refer to each other (an
# AdditionalDocumentResponse's own `extracted` field is a full
# ExtractedInvoiceResponse), so one of the two names has to be a forward
# reference until both classes actually exist.
ExtractedInvoiceResponse.model_rebuild()
