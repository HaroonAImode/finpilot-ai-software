import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, ARRAY, Date, DateTime, Enum, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# The header/single-value fields raw_extraction_json carries a per-field
# {value, confidence, method, page, bbox} entry for — matches
# ExtractedInvoiceSchema's own field set (ai-engine's response contract).
_CONFIDENCE_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "ntn", "subtotal", "tax_rate", "tax_amount", "total",
)

#: Receipt/bill robustness additions (docs/invoice-ocr-plan.md) — document-
#: wide detections (currency, city, country, document type, payment
#: status) plus customer_name, none of which are edit-via-PUT columns yet
#: (unlike vendor_name/total/etc. above) — read-only, derived from
#: raw_extraction_json the same way field_confidence is, via
#: Invoice.extracted_fields below.
_EXTRACTED_FIELDS = ("customer_name", "currency", "document_type", "payment_status", "city", "country")

#: Saved Records' categorized cashbook view (docs/superpowers/specs/
#: 2026-09-01-saved-records-cashbook-design.md) — the first nine are the
#: uploaded cashbook's own category headings verbatim (typos corrected,
#: wording tidied: "Maintinance" -> "Maintenance"), in the same order they
#: appeared there; the rest are common SME purchase categories that
#: particular month didn't happen to use. A suggestion, not an enforced
#: enum — same "SUGGESTED_*" convention as SUGGESTED_DEPARTMENTS (HR
#: Service) and vendors-service's SUGGESTED_CATEGORIES: a real company's
#: categories never fit a fixed list, and a new one must not need a
#: migration. category_classifier.py's keyword table targets these exact
#: strings — keep the two in sync if either changes.
SUGGESTED_CATEGORIES = (
    "Office Entertainment", "Employee Care", "Office / Misc Supplies", "Stationary / Others",
    "Employee Training & Education", "Legal & Professional", "Office Repair & Maintenance",
    "Vehicle Running & Maintenance", "Salary",
    "Utilities", "Rent", "Marketing", "Raw Material", "Fuel & Transport", "Travel", "Other",
)


class InvoiceType(str, enum.Enum):
    purchase = "purchase"
    # Not produced by anything in Phase 3 (the AI Scanner only ever
    # extracts a *received* invoice) — declared now so the column's enum
    # type doesn't need an ALTER TYPE migration once the Invoice Generator
    # (architecture report §5.3, sales invoices) is built.
    sale = "sale"


class PaymentMethod(str, enum.Enum):
    #: Never inferred by the rules engine — a human picks this after
    #: reviewing the extraction, same as any other correction.
    bank = "bank"
    cash = "cash"


class InvoiceStatus(str, enum.Enum):
    #: The rules engine (docs/invoice-ocr-plan.md §2a) was confident enough
    #: across every critical field and the arithmetic checks passed.
    processed = "processed"
    needs_review = "needs_review"
    needs_review_high_priority = "needs_review_high_priority"
    #: A human confirmed the (possibly corrected) data is right.
    validated = "validated"
    #: Booked — Transactions Service doesn't exist yet (architecture report
    #: §11b's own note applies here too), so this is a local status flip
    #: today, not a real hand-off; see invoice_service.py.
    sent_to_accounting = "sent_to_accounting"
    #: A human decided this document should not be processed at all. A soft
    #: state, deliberately not a row delete: the source file, the extraction
    #: and the audit trail all stay intact and recoverable, and rejecting is
    #: reversible. Permanent deletion, if it is ever wanted, is a separate
    #: explicit action — this service has no delete endpoint at all today.
    rejected = "rejected"


class TransactionStatus(str, enum.Enum):
    """Whether a document belongs in financial transaction processing —
    deliberately a *separate axis* from `InvoiceStatus`, which tracks where
    a document is in the human workflow (needs_review -> validated ->
    sent_to_accounting, or rejected).

    Keeping them apart is what avoids duplicating the review/rejected
    concepts that already exist: "a confirmed internal memo" is simply
    transaction_status=non_transactional with status=validated, needing no
    new state of its own, and "requires review" stays entirely InvoiceStatus'
    job rather than being restated here.
    """

    transactional = "transactional"
    #: Internal paperwork that carries amounts but is not a purchase/sale —
    #: a minute sheet, an approval request. Never force-fitted into a
    #: cashbook category; see Invoice.amount_mentioned.
    non_transactional = "non_transactional"


class ClassificationSource(str, enum.Enum):
    """Who decided `transaction_status`. A human correction never silently
    overwrites the deterministic result — the rules engine's original
    classification stays in `raw_extraction_json` (Rule 8.1), and this
    records that a person disagreed, which is what makes the override
    auditable and future rule improvements possible.
    """

    rule = "rule"
    user_override = "user_override"


class Invoice(Base):
    """One scanned/purchase invoice. Extracted fields are editable (a human
    can correct them after review) — the untouched extraction result lives
    separately in `raw_extraction_json`, per
    docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md Rule 8.1:
    "never overwrite the raw extraction," so a correction is always
    auditable against what the rules engine originally produced.
    """

    __tablename__ = "invoice"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[InvoiceType] = mapped_column(
        Enum(InvoiceType, name="invoice_type"), nullable=False, default=InvoiceType.purchase
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"), nullable=False, default=InvoiceStatus.needs_review
    )

    # Editable fields — start out exactly as extracted, may be corrected by
    # a human via PUT /invoices/{id}. All nullable: the rules engine
    # legitimately doesn't find every field on every document (see
    # docs/invoice-ocr-plan.md's real-document findings), and forcing a
    # value here would mean inventing one.
    vendor_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The Vendors Service record this invoice was matched to, once one
    #: exists. A plain UUID, not a FK — that table lives in another
    #: service's database (same convention the connectors use). NULL means
    #: "not linked yet", which every invoice scanned before Vendors Service
    #: existed legitimately is; `vendor_name` still holds the raw OCR
    #: evidence either way (Rule 8.1).
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Who a *sales* invoice is billed to. The purchase half of this model uses
    #: vendor_name (who it came from); a generated sales invoice uses this
    #: instead. The architecture report's own model calls for
    #: "vendor_id or customer_id" — this is the free-text half of that.
    customer_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Saved Records' cashbook category (Office Entertainment, Employee
    #: Care, etc. — see SUGGESTED_CATEGORIES above). Purchase invoices
    #: only in practice; never set by the rules engine, always a human
    #: choice made after review, same as payment_method. NULL groups as
    #: "Uncategorized" wherever this is displayed or aggregated.
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Whether this document belongs in financial transaction processing —
    #: see TransactionStatus. Defaults to transactional so every row that
    #: existed before this column, and every document whose type the engine
    #: could not determine, keeps behaving exactly as it did.
    transaction_status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="invoice_transaction_status"),
        nullable=False, default=TransactionStatus.transactional, server_default=TransactionStatus.transactional.value,
    )
    #: Who decided transaction_status — see ClassificationSource.
    classification_source: Mapped[ClassificationSource] = mapped_column(
        Enum(ClassificationSource, name="invoice_classification_source"),
        nullable=False, default=ClassificationSource.rule, server_default=ClassificationSource.rule.value,
    )
    #: An amount a non-transactional document *states* ("For approval of Rs.
    #: 22,875/-"). Deliberately not `total`: a minute sheet has no
    #: transaction total, and letting its stated amount occupy `total` is
    #: exactly how an internal memo becomes a booked expense — it would flow
    #: into category assignment, vendor spend, the cashbook totals and the
    #: accounting hand-off, every one of which reads `total`. Displayed as
    #: "Amount mentioned"; promoted to `total` only if a human reclassifies
    #: the document as transactional.
    amount_mentioned: Mapped[float | None] = mapped_column(Float, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ntn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: How this invoice was paid — bank or cash. Never extracted by the
    #: rules engine (nothing in raw_extraction_json backs it, unlike the
    #: fields above); a human sets it manually after reviewing the scan.
    payment_method: Mapped[PaymentMethod | None] = mapped_column(
        Enum(PaymentMethod, name="invoice_payment_method"), nullable=True,
    )

    # Extraction metadata — never edited by a human, always reflects what
    # the most recent scan actually produced.
    document_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_source: Mapped[str] = mapped_column(String(16), nullable=False, default="pdf_text")
    review_flags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String).with_variant(JSON(), "sqlite")), nullable=False, default=list,
    )
    #: The complete AI Engine response, untouched — Rule 8.1's audit trail.
    #: A JSON blob rather than normalized columns because its whole purpose
    #: is "what did the engine originally say," not something queried by field.
    #: NULL for a generated sales invoice: nothing was extracted, so there is
    #: no extraction record. An empty dict would be indistinguishable from a
    #: real scan that found nothing, which Rule 8.1's audit trail depends on
    #: being able to tell apart.
    raw_extraction_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Source file
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mimetype: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    s3_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: SHA-256 of the uploaded file — Rule 7.1's cheap, exact duplicate
    #: check, enforced per company (see the migration's unique constraint).
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position",
    )

    @property
    def field_confidence(self) -> dict[str, float]:
        """Per-field confidence (0.0-1.0), derived from raw_extraction_json
        — the review UI's whole reason for existing is showing a human
        which auto-filled values to trust and which to double-check (Rule
        8.2), so this has to reach the API response, not just live in the
        audit blob. A plain Python property (not a DB column) since it is
        fully derived from data already stored; Pydantic's from_attributes
        picks it up the same way it reads any other attribute here.
        """
        raw = self.raw_extraction_json or {}
        result: dict[str, float] = {}
        for field_name in _CONFIDENCE_FIELDS:
            entry = raw.get(field_name)
            # Excluded when value is None — a field the engine never
            # actually resolved (not_found, or found-but-failed-validation)
            # has nothing to show confidence *for*; a blank input with a
            # "12%" badge next to it would just be confusing. "Missing from
            # this dict" is itself the review UI's signal to treat the
            # field as unfilled, not "confidence 0".
            if isinstance(entry, dict) and entry.get("value") is not None and "confidence" in entry:
                result[field_name] = entry["confidence"]
        return result

    @property
    def field_locations(self) -> dict[str, dict]:
        """Per-field {page, bbox} for the 8 header/scalar fields, derived
        from raw_extraction_json exactly like field_confidence — the
        bounding-box review UI (second scanner experience) reads this to
        know where on the document each value came from, without exposing
        the raw extraction blob itself (method strings, etc.) to the normal
        user-facing API. Same exclusion rule as field_confidence: a field
        the engine never resolved, or one missing location data, has no
        entry at all.
        """
        raw = self.raw_extraction_json or {}
        result: dict[str, dict] = {}
        for field_name in _CONFIDENCE_FIELDS:
            entry = raw.get(field_name)
            if (
                isinstance(entry, dict) and entry.get("value") is not None
                and entry.get("bbox") is not None and entry.get("page") is not None
            ):
                result[field_name] = {"page": entry["page"], "bbox": entry["bbox"]}
        return result

    @property
    def page_dimensions(self) -> list[list[float]]:
        """(width, height) per page, straight from raw_extraction_json —
        [] for an invoice scanned before this field existed, which the
        review UI treats as "grounded preview not available" rather than
        erroring."""
        raw = self.raw_extraction_json or {}
        return raw.get("page_dimensions") or []

    @property
    def extracted_fields(self) -> dict[str, dict]:
        """Value + status for the receipt/bill robustness fields (customer
        name, currency, document type, payment status, city, country) —
        read-only for now (not yet correctable via PUT, unlike vendor_name/
        total/etc.), derived from raw_extraction_json the same
        exclude-when-absent way field_confidence is. A field with no
        evidence found is simply absent from this dict — e.g. currency is
        never fabricated as "USD" just because raw_extraction_json has no
        entry for it.
        """
        raw = self.raw_extraction_json or {}
        result: dict[str, dict] = {}
        for field_name in _EXTRACTED_FIELDS:
            entry = raw.get(field_name)
            if isinstance(entry, dict) and entry.get("value") is not None:
                result[field_name] = {
                    "value": entry["value"],
                    "confidence": entry.get("confidence", 0.0),
                    "status": entry.get("status") or ("FOUND" if entry.get("confidence", 0.0) >= 0.5 else "UNCERTAIN"),
                }
        return result

    @property
    def detected_document_type(self) -> str | None:
        """What the rules engine decided this document *is* ("minute_sheet",
        "invoice", …), read straight out of raw_extraction_json.

        Surfaced as its own scalar (rather than only inside
        `extracted_fields`) because the Non-Transactional Documents area
        needs it on the *list* payload, and because it must keep reporting
        the engine's original verdict even after a human overrides
        `transaction_status` — Rule 8.1 again: the correction is recorded in
        `classification_source`, the original detection is never rewritten.
        """
        entry = (self.raw_extraction_json or {}).get("document_type")
        value = entry.get("value") if isinstance(entry, dict) else None
        return value if isinstance(value, str) else None

    @property
    def classification_reason(self) -> str | None:
        """The document's own wording behind `detected_document_type`,
        quoted verbatim ("For approval of Rs. 22,875/-") — shown to a
        reviewer so the classification is explainable rather than an opaque
        label they are asked to trust."""
        reason = (self.raw_extraction_json or {}).get("classification_reason")
        return reason if isinstance(reason, str) else None

    @property
    def dynamic_fields(self) -> list[dict]:
        """Vendor-specific labelled fields the rules engine found but has no
        canonical name for — read straight out of raw_extraction_json, the
        same derived-property approach as field_confidence above.

        Each entry keeps the vendor's own printed `label` next to the
        normalised `key`, and its `bbox`/`page` where the value was read
        from, so the review UI can highlight it on the document exactly
        like a canonical field. [] for an invoice scanned before generic
        discovery existed, which reads as "none found" rather than an
        error."""
        raw = self.raw_extraction_json or {}
        entries = raw.get("dynamic_fields") or []
        return [
            {
                "key": e["key"],
                "label": e.get("label", e["key"]),
                "value": e.get("value"),
                "confidence": e.get("confidence", 0.0),
                "page": e.get("page"),
                "bbox": e.get("bbox"),
            }
            for e in entries
            if isinstance(e, dict) and e.get("key") and e.get("value") is not None
        ]
