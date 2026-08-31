"""Revenue Manager — scan-to-revenue. Design spec:
docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md.

Mirrors scanner.py's purchase-scan flow exactly, aimed at `type=sale`
instead — same AI Engine call, same rules-based extraction, same
LiteParse -> PaddleOCR -> Tesseract fallback chain, untouched. This does
**not** replace the Invoice Generator (`sales.py`, authored sales invoices
via `POST /invoices/sales/`) — scanner.py's own docstring already commits
"sales invoices are generated, not uploaded" for that surface, and that
stays true. This is an *additional* creation path: an SME's real sales
records just as often arrive as a document (a POS receipt, a delivery
challan, an invoice they already issued elsewhere) as they get typed here.

Registered in main.py **before** sales_router — its literal paths
(`/scan`, `/scan/{job_id}`, `/scanned`, `/summary`) must be matched before
sales_router's `/{invoice_id}` pattern gets a chance to treat any of them
as an invoice id, the same ordering reason sales_router itself documents
against invoices_router.
"""
import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import AIJob, AIJobStatus, Invoice, InvoiceStatus, InvoiceType
from app.schemas.invoice import InvoiceListItem, InvoiceListResponse, InvoiceResponse, ScanJobResponse
from app.schemas.sales_summary import (
    MonthlyRevenuePoint, SalesSummaryResponse, SalesSummaryTotals, TopCustomerPoint,
)
from app.services.ai_engine_client import AIEngineError, extract_invoice_data
from app.services.invoice_builder import build_invoice
from app.services.storage import StorageManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/invoices/sales", tags=["sales-scanner"])

#: A scanned sale still awaiting a human's confirmation before its numbers
#: should be trusted as real revenue.
_PENDING_REVIEW_STATUSES = (InvoiceStatus.needs_review, InvoiceStatus.needs_review_high_priority)


@router.post("/scan", response_model=ScanJobResponse)
async def scan_sales_invoice(
    file: UploadFile,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ScanJobResponse:
    """Mirrors scanner.py's scan_invoice exactly, aimed at `type=sale`.

    No `known_vendors` — the rules engine's fuzzy-match is a purchase-side
    concept (Rule 3.4/7.3, matching against this company's own vendor
    list); a sale's counterparty is a customer, and there is no customer
    directory here to match against (see the design spec §4's v1
    limitations — `known_customers` is explicitly out of scope, since
    adding it would mean touching AI Engine, which this feature does not).
    """
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit")

    file_hash = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(Invoice)
        .where(
            Invoice.company_id == company_id, Invoice.file_hash == file_hash,
            # Scoped to sale: a purchase invoice and a sales document could
            # coincidentally share bytes only in a contrived test, but
            # scoping here means a purchase-side dedup hit can never be
            # handed back as if it were a scanned sale.
            Invoice.type == InvoiceType.sale,
        )
        .options(selectinload(Invoice.items))
    )
    if existing is not None:
        # Rule 7.1 — this exact file was already scanned for this company.
        job = AIJob(company_id=company_id, invoice_id=existing.id, status=AIJobStatus.done, completed_at=datetime.now(timezone.utc))
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return ScanJobResponse(
            job_id=job.id, status="done", invoice_id=existing.id,
            invoice=InvoiceResponse.model_validate(existing),
        )

    job = AIJob(company_id=company_id, status=AIJobStatus.processing)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        extraction = await extract_invoice_data(
            settings, filename=file.filename or "upload", content=content, mimetype=file.content_type,
        )
    except AIEngineError as exc:
        job.status = AIJobStatus.failed
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    storage = StorageManager(settings)
    try:
        s3_key, _ = await storage.store_bytes(str(job.id), file.filename or "upload", content)
    except Exception as exc:
        job.status = AIJobStatus.failed
        job.error = f"Extraction succeeded but the file could not be stored: {exc}"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=502, detail="Could not store the uploaded file") from exc

    invoice = build_invoice(
        company_id=company_id, extraction=extraction, filename=file.filename or "upload",
        mimetype=file.content_type, size=len(content), s3_key=s3_key, file_hash=file_hash,
        invoice_type=InvoiceType.sale,
    )
    db.add(invoice)
    await db.flush()

    job.invoice_id = invoice.id
    job.status = AIJobStatus.done
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])

    logger.info(
        "Sales invoice scanned",
        extra={"invoice_id": str(invoice.id), "status": invoice.status.value, "confidence": round(invoice.document_confidence, 3)},
    )
    return ScanJobResponse(job_id=job.id, status="done", invoice_id=invoice.id, invoice=InvoiceResponse.model_validate(invoice))


@router.get("/scan/{job_id}", response_model=ScanJobResponse)
async def get_sales_scan_status(
    job_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ScanJobResponse:
    """Identical to scanner.py's get_scan_status — AIJob has no `type`
    column and needs none; a job id already only ever resolves to the
    invoice it was created for."""
    job = await db.scalar(select(AIJob).where(AIJob.id == job_id, AIJob.company_id == company_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    invoice_response = None
    if job.invoice_id:
        invoice_row = await db.scalar(
            select(Invoice).where(Invoice.id == job.invoice_id).options(selectinload(Invoice.items))
        )
        if invoice_row:
            invoice_response = InvoiceResponse.model_validate(invoice_row)

    return ScanJobResponse(
        job_id=job.id, status=job.status.value, invoice_id=job.invoice_id, invoice=invoice_response, error=job.error,
    )


def _scanned_sales_query(company_id: UUID):
    # extraction_source != "generated" is the discriminator between a
    # scanned sale and one authored through the Invoice Generator
    # (sales.py) — both share `type=sale` and this one table, by design
    # (§5 of the spec: one ledger, not a parallel table every future report
    # would have to UNION).
    return select(Invoice).where(
        Invoice.company_id == company_id,
        Invoice.type == InvoiceType.sale,
        Invoice.extraction_source.isnot(None),
        Invoice.extraction_source != "generated",
    )


@router.get("/scanned", response_model=InvoiceListResponse)
async def list_scanned_sales(
    status: Optional[str] = Query(
        None, description="Filter by status, or a comma-separated list, or omit for all",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    query = _scanned_sales_query(company_id)
    if status:
        try:
            statuses = [InvoiceStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
        query = query.where(Invoice.status.in_(statuses))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return InvoiceListResponse(
        invoices=[InvoiceListItem.model_validate(row) for row in rows], total=total or 0, skip=skip, limit=limit,
    )


@router.get("/summary", response_model=SalesSummaryResponse)
async def sales_summary(
    date_from: Optional[date] = Query(
        None, description="Inclusive lower bound on the resolved bucket date. Omit for no lower bound.",
    ),
    date_to: Optional[date] = Query(
        None, description="Inclusive upper bound on the resolved bucket date. Omit for no upper bound.",
    ),
    customer_limit: int = Query(
        5, ge=1, le=1000,
        description="How many top customers to return, sorted by revenue descending. "
                    "Default matches the Dashboard/Revenue Manager charts' own cap; "
                    "Reports Service's Sales Report passes a much larger value for a full breakdown.",
    ),
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> SalesSummaryResponse:
    """Revenue Manager's KPI row and both charts, in one call — also
    Reports Service's own source for period-scoped revenue (Profit & Loss,
    Cash Flow, Tax Summary, Sales Report), via `date_from`/`date_to`.

    Aggregated in Python, not SQL: row counts here are bounded by "sales
    documents actually scanned so far," not the whole ledger, and this
    service's own test suite runs on SQLite — the same reasoning
    `invoices.py::unlinked_vendor_groups` already used for the same
    tradeoff. Filtering happens here too, in Python, against the same
    resolved `bucket_date` the monthly chart already uses (not a SQL
    WHERE on `invoice_date` alone) — a document whose date only survived
    via the `created_at` fallback must be included or excluded on that
    same resolved date, not silently dropped from a period filter it
    would otherwise fall inside.
    """
    rows = (
        await db.execute(
            _scanned_sales_query(company_id).with_only_columns(
                Invoice.total, Invoice.invoice_date, Invoice.created_at, Invoice.customer_name, Invoice.status,
            )
        )
    ).all()

    monthly: dict[str, list[float]] = defaultdict(list)
    by_customer: dict[str, list[float]] = defaultdict(list)
    revenue = 0.0
    pending_review = 0
    today_revenue = 0.0
    document_count = 0
    today = datetime.now(timezone.utc).date()

    for total, invoice_date, created_at, customer_name, status in rows:
        # invoice_date is what the document itself says happened; created_at
        # (when it was scanned) is the fallback for a document where that
        # date extraction failed — either beats silently dropping the row
        # from the monthly chart entirely.
        bucket_date = invoice_date or (created_at.date() if created_at else None)

        if date_from is not None or date_to is not None:
            if bucket_date is None:
                continue
            if date_from is not None and bucket_date < date_from:
                continue
            if date_to is not None and bucket_date > date_to:
                continue

        amount = total or 0.0
        revenue += amount
        document_count += 1
        if status in _PENDING_REVIEW_STATUSES:
            pending_review += 1

        if bucket_date:
            monthly[bucket_date.strftime("%Y-%m")].append(amount)
            if bucket_date == today:
                today_revenue += amount

        # Blank/null grouped under one explicit label rather than one
        # silent bucket per missing name — "Unattributed" is a real,
        # visible signal that customer extraction is failing on some
        # documents, not just an absent chart segment.
        by_customer[customer_name.strip() if customer_name and customer_name.strip() else "Unattributed"].append(amount)

    monthly_points = [
        MonthlyRevenuePoint(month=month, total=round(sum(amounts), 2), count=len(amounts))
        for month, amounts in sorted(monthly.items())
    ]
    top_customers = sorted(
        (
            TopCustomerPoint(customer_name=name, total=round(sum(amounts), 2), count=len(amounts))
            for name, amounts in by_customer.items()
        ),
        key=lambda point: point.total, reverse=True,
    )[:customer_limit]

    return SalesSummaryResponse(
        monthly=monthly_points,
        top_customers=top_customers,
        totals=SalesSummaryTotals(
            revenue=round(revenue, 2), document_count=document_count, pending_review=pending_review,
            today_revenue=round(today_revenue, 2),
        ),
    )
