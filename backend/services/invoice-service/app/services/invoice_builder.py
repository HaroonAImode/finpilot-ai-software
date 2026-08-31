"""Maps AI Engine's extraction result onto the Invoice/InvoiceItem models —
the one place that translation happens, so the mapping is defined once.
"""
from datetime import date, datetime
from uuid import UUID

from app.models import ClassificationSource, Invoice, InvoiceItem, InvoiceStatus, InvoiceType, TransactionStatus
from app.schemas.ai_extraction import ExtractedInvoiceSchema
from app.services.category_classifier import classify_category

# AI Engine's review_status vocabulary -> this service's InvoiceStatus.
# Kept as an explicit map (not a shared enum) since the two services are
# allowed to evolve their vocabularies independently — see
# ai_extraction.py's module docstring for the same reasoning.
_REVIEW_STATUS_MAP: dict[str, InvoiceStatus] = {
    "auto_processed": InvoiceStatus.processed,
    "needs_review": InvoiceStatus.needs_review,
    "needs_review_high_priority": InvoiceStatus.needs_review_high_priority,
}


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def build_invoice(
    *, company_id: UUID, extraction: ExtractedInvoiceSchema, filename: str, mimetype: str | None,
    size: int, s3_key: str, file_hash: str, invoice_type: InvoiceType = InvoiceType.purchase,
) -> Invoice:
    """Builds an unsaved Invoice (with its InvoiceItem children attached)
    from an AI Engine extraction result. Caller adds it to the session and
    commits — this function has no DB dependency, so it's trivially unit
    testable against a fixed ExtractedInvoiceSchema.

    `invoice_type` defaults to purchase (the scanner's original and only
    caller) so every existing call site is unaffected. The Revenue
    Manager's scan-to-revenue path (docs/superpowers/specs/2026-08-27-
    revenue-manager-scan-design.md) passes `InvoiceType.sale` — the same
    rules-engine output, aimed at a different column.

    `customer_name` is sourced with a `vendor_name` fallback for a sale:
    the rules engine's name heuristic finds "the most prominent company
    name that isn't obviously a total/label," which on a sales document is
    usually the bill-to customer — but it is reported under the engine's
    `vendor_name` key regardless of which party it actually is.
    `customer_name` is a secondary, less reliable detector. Taking
    `customer_name` first and falling back to `vendor_name` gets the
    counterparty right either way, while `vendor_name` itself is still set
    unconditionally below so its confidence/bbox keep working for the
    bounding-box overlay with no special-casing there.
    """
    vendor_name = _str_or_none(extraction.vendor_name.value)
    # "Is this a financial transaction document?" is answered before "which
    # cashbook category does it belong to?" — an internal minute sheet or
    # approval request has no meaningful purchase category, and asking for
    # one only ever produces a wrong guess or a misleading "Uncategorized".
    transactional = extraction.transactional
    transaction_status = (
        TransactionStatus.transactional if transactional else TransactionStatus.non_transactional
    )
    # Best-guess only, purchases only, transactional only — a sale has no
    # cashbook category, it has a customer and line items (see
    # Invoice.category's own docstring). Still just a starting point: Saved
    # Records lets a human recategorize or clear it at any time, same as
    # every other SUGGESTED_* field here.
    category = (
        classify_category(
            vendor_name=vendor_name, filename=filename,
            item_descriptions=[_str_or_none(item.description.value) for item in extraction.line_items],
            # P1-E.1 (docs/invoice-ocr-plan.md §25): the document's own raw
            # OCR text — real category evidence a document states only in
            # free text (a cash-receipt voucher's "as Salary/ Stipend")
            # never reaches vendor_name/line_items at all.
            raw_text=extraction.raw_text,
        )
        if invoice_type is InvoiceType.purchase and transactional else None
    )

    invoice = Invoice(
        company_id=company_id,
        type=invoice_type,
        status=_REVIEW_STATUS_MAP[extraction.review_status],
        transaction_status=transaction_status,
        # Always the rule's own verdict at scan time; only a human action
        # (POST /reclassify) ever changes this to user_override.
        classification_source=ClassificationSource.rule,
        amount_mentioned=_float_or_none(extraction.amount_mentioned),
        vendor_name=vendor_name,
        category=category,
        customer_name=(
            _str_or_none(extraction.customer_name.value) or _str_or_none(extraction.vendor_name.value)
            if invoice_type is InvoiceType.sale else None
        ),
        invoice_number=_str_or_none(extraction.invoice_number.value),
        invoice_date=_parse_date(extraction.invoice_date.value),
        ntn=_str_or_none(extraction.ntn.value),
        subtotal=_float_or_none(extraction.subtotal.value),
        tax_rate=_float_or_none(extraction.tax_rate.value),
        tax_amount=_float_or_none(extraction.tax_amount.value),
        total=_float_or_none(extraction.total.value),
        document_confidence=extraction.document_confidence,
        extraction_source=extraction.extraction_source,
        review_flags=list(extraction.review_flags),
        raw_extraction_json=extraction.model_dump(mode="json"),
        filename=filename,
        mimetype=mimetype,
        size=size,
        s3_key=s3_key,
        file_hash=file_hash,
    )
    invoice.items = [
        InvoiceItem(
            position=i,
            description=_str_or_none(item.description.value),
            qty=_float_or_none(item.qty.value),
            rate=_float_or_none(item.rate.value),
            amount=_float_or_none(item.amount.value),
            arithmetic_check=item.arithmetic_check,
            review_flags=list(item.review_flags),
        )
        for i, item in enumerate(extraction.line_items)
    ]
    return invoice
