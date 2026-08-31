from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InvoiceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    description: Optional[str] = None
    qty: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    arithmetic_check: str
    review_flags: list[str]


class ReclassifyRequest(BaseModel):
    """The human override behind POST /invoices/{id}/reclassify."""

    transaction_status: Literal["transactional", "non_transactional"]


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    #: "transactional" | "non_transactional" — whether this document
    #: belongs in financial transaction processing at all. A separate axis
    #: from `status`, which tracks the human workflow.
    transaction_status: str = "transactional"
    #: "rule" | "user_override" — who decided transaction_status. The rules
    #: engine's original verdict always stays in raw_extraction_json.
    classification_source: str = "rule"
    #: An amount a non-transactional document states ("For approval of Rs.
    #: 22,875/-"). Never a transaction total — see the model's own field
    #: docstring for why the two are deliberately kept apart.
    amount_mentioned: Optional[float] = None
    #: What the rules engine decided this document is ("minute_sheet",
    #: "invoice", ...). Keeps reporting the engine's own verdict even after
    #: a human overrides transaction_status.
    detected_document_type: Optional[str] = None
    #: The document's own wording behind that decision, quoted verbatim.
    classification_reason: Optional[str] = None
    vendor_name: Optional[str] = None
    #: The Vendors Service record this invoice is linked to, or null when it
    #: has not been matched yet.
    vendor_id: Optional[UUID] = None
    #: Set on generated sales invoices; NULL on scanned purchase invoices,
    #: which record who the document came *from* in vendor_name instead.
    customer_name: Optional[str] = None
    #: Saved Records' cashbook category — see Invoice.category. None until
    #: a human sets one; grouped/displayed as "Uncategorized" until then.
    category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    ntn: Optional[str] = None
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    #: How this invoice was paid — never extracted by the rules engine, set
    #: manually by a human after reviewing the scan. None until they choose.
    payment_method: Optional[str] = None
    document_confidence: float
    #: Per-field confidence (0.0-1.0), keyed by field name — Invoice.field_confidence,
    #: derived from raw_extraction_json. Only fields the rules engine actually
    #: found have an entry; a field missing here means "not found at all",
    #: not "low confidence" — the review UI should tell those two apart.
    field_confidence: dict[str, float]
    #: Per-field {page, bbox}, for the bounding-box review UI — see
    #: Invoice.field_locations. Same "missing means not found" rule as
    #: field_confidence.
    field_locations: dict[str, dict]
    #: (width, height) per page, native units matching field_locations'
    #: bbox space — [] for an invoice scanned before this field existed.
    page_dimensions: list[tuple[float, float]] = []
    #: Receipt/bill robustness additions (customer_name, currency,
    #: document_type, payment_status, city, country) — see
    #: Invoice.extracted_fields. Each present entry is
    #: {"value", "confidence", "status"}; a field with no evidence found is
    #: simply absent, never a fabricated default.
    extracted_fields: dict[str, dict] = {}
    #: Invoice.dynamic_fields — vendor-specific labelled fields with no
    #: canonical name here, each carrying the document's own printed label
    #: and the bbox its value was read from.
    dynamic_fields: list[dict] = []
    extraction_source: str
    review_flags: list[str]
    #: Optional because a generated sales invoice has no source document —
    #: nothing was uploaded, so there is no filename, mimetype or size.
    #: See migration 20260826_01 for why these are NULL rather than faked.
    filename: Optional[str] = None
    mimetype: Optional[str] = None
    size: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    items: list[InvoiceItemResponse]


class InvoiceListItem(BaseModel):
    """Lighter shape for the list view — no line items, no raw extraction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    #: "transactional" | "non_transactional" — whether this document
    #: belongs in financial transaction processing at all. A separate axis
    #: from `status`, which tracks the human workflow.
    transaction_status: str = "transactional"
    #: "rule" | "user_override" — who decided transaction_status. The rules
    #: engine's original verdict always stays in raw_extraction_json.
    classification_source: str = "rule"
    #: An amount a non-transactional document states ("For approval of Rs.
    #: 22,875/-"). Never a transaction total — see the model's own field
    #: docstring for why the two are deliberately kept apart.
    amount_mentioned: Optional[float] = None
    #: What the rules engine decided this document is ("minute_sheet",
    #: "invoice", ...). Keeps reporting the engine's own verdict even after
    #: a human overrides transaction_status.
    detected_document_type: Optional[str] = None
    #: The document's own wording behind that decision, quoted verbatim.
    classification_reason: Optional[str] = None
    vendor_name: Optional[str] = None
    #: Present so the Saved Records grid can show and edit it without a
    #: per-row detail fetch — the grid is a spreadsheet over the list.
    customer_name: Optional[str] = None
    category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    ntn: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    payment_method: Optional[str] = None
    document_confidence: float
    #: Absent for a generated sales invoice — see InvoiceResponse.filename.
    filename: Optional[str] = None
    #: For Saved Records' preview-kind detection (image vs. PDF vs.
    #: unsupported) without a per-row detail fetch — same reasoning as
    #: filename above.
    mimetype: Optional[str] = None
    created_at: datetime


class InvoiceListResponse(BaseModel):
    invoices: list[InvoiceListItem]
    total: int
    skip: int
    limit: int


class InvoiceItemUpdate(BaseModel):
    description: Optional[str] = None
    qty: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None


class InvoiceUpdateRequest(BaseModel):
    """A human correction after review. Every field is optional and only
    the ones actually present in the request are applied — same
    model_fields_set convention used across this codebase's other services
    — so an omitted field is left alone rather than reset to null, while an
    explicit `null` still clears it."""

    vendor_name: Optional[str] = None
    #: Links this invoice to a Vendors Service record. Set by a human (or a
    #: future matcher) — never by the rules engine, which only ever reads a
    #: name off the page.
    vendor_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    #: One of Invoice.SUGGESTED_CATEGORIES, or any other free text — not
    #: validated against the list server-side, same as vendor_name/
    #: department elsewhere in this codebase. An explicit null clears it
    #: back to "Uncategorized".
    category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    ntn: Optional[str] = None
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    payment_method: Optional[Literal["bank", "cash"]] = None
    #: Deliberately limited to the two *saved* states. The Saved Records grid
    #: lets an admin move a record between them, but it must not be a back
    #: door into the extraction lifecycle: an invoice still in needs_review
    #: has to go through the Scanner's own guards (and
    #: POST /send-to-accounting keeps its 409 for un-validated invoices)
    #: rather than being typed straight into "sent_to_accounting".
    status: Optional[Literal["validated", "sent_to_accounting"]] = None
    items: Optional[list[InvoiceItemUpdate]] = None


class ScanJobResponse(BaseModel):
    job_id: UUID
    status: str
    invoice_id: Optional[UUID] = None
    invoice: Optional[InvoiceResponse] = None
    error: Optional[str] = None
    #: Document Preprocessing (docs/invoice-ocr-plan.md §7) — populated only
    #: when this one upload's photo confidently contained more than one
    #: separate document, each of which got its own AIJob/Invoice the same
    #: way the primary one did. Empty for the overwhelming common case, so
    #: a caller that only ever reads invoice_id/invoice sees exactly
    #: today's behavior, unchanged.
    additional_invoice_ids: list[UUID] = []


class VendorSpend(BaseModel):
    """One vendor's aggregated purchase spend, for Vendors Service.

    Returned in bulk by GET /invoices/vendor-spend so that service can
    derive total_spend_pkr in a single cross-service call rather than one
    per vendor.
    """

    vendor_id: UUID
    vendor_name: Optional[str] = None
    total_spend: float
    invoice_count: int


class UnlinkedVendorGroup(BaseModel):
    """Every purchase invoice sharing one raw `vendor_name`, none of them
    linked to a vendor record yet. For Vendors Service's reconciliation
    queue (§3.8) — grouped here because the accountant reviews a name once,
    not once per invoice that happens to carry it.
    """

    vendor_name: str
    invoice_ids: list[UUID]
    invoice_count: int
    total_amount: float
    sample_invoice_number: Optional[str] = None
    latest_invoice_date: Optional[date] = None


class BulkLinkVendorRequest(BaseModel):
    invoice_ids: list[UUID] = Field(min_length=1)
    vendor_id: UUID


class BulkLinkVendorResponse(BaseModel):
    updated_count: int


class InvoiceOptions(BaseModel):
    """Suggested cashbook categories for Saved Records. Deliberately not
    enforced — see Invoice.SUGGESTED_CATEGORIES for why."""

    categories: list[str]


class CategorySummary(BaseModel):
    """One category's aggregated count/total across every matching purchase
    invoice — computed by a single SQL GROUP BY
    (GET /invoices/categories/summary), never re-summed client-side. This is
    the one number Saved Records' per-category totals and the category PDF
    report's totals row both read from, so the two can never disagree.
    """

    #: None represents every invoice with no category set — shown as
    #: "Uncategorized" by every caller, never silently dropped from the total.
    category: Optional[str] = None
    count: int
    total: float


class MonthlyPaymentSummary(BaseModel):
    """Saved Records' Cash Book summary strip (GET /invoices/monthly-
    summary) — the same single SQL GROUP BY discipline as CategorySummary
    above (never re-summed client-side), grouped by `payment_method`
    instead of `category`. `cash_total + online_total + unspecified_total`
    always equals `monthly_total` by construction: every row counted here
    falls into exactly one of the three buckets.
    """

    #: The sum of every matching invoice's `total`, regardless of payment
    #: method — the Cash Book's own grand total for the period.
    monthly_total: float
    #: Rows with payment_method == cash.
    cash_total: float
    #: Rows with payment_method == bank — shown to the user as "Online"
    #: (see PAYMENT_METHOD_LABELS on the frontend); the stored enum value
    #: is unchanged to avoid a migration for what is purely a display term.
    online_total: float
    #: Rows with payment_method IS NULL — a human has not yet said how this
    #: was paid. Never silently folded into cash or online.
    unspecified_total: float
    #: How many invoices contributed to monthly_total — distinct from any
    #: category's own count, since a transaction spans exactly one payment
    #: method bucket but may or may not have a category set.
    transaction_count: int
