"""Invoice Generator — architecture report §5.3's *sales* half.

The mirror image of the scanner: instead of reading a document someone sent
us and inferring fields, this records fields a user authored and produces a
document. Nothing here has a confidence score, because nothing is a guess.

Shares the `invoice` table with the purchase side, discriminated by
`Invoice.type`. Same company, same ledger, one place to look — rather than a
parallel table that every future report would have to UNION.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Invoice, InvoiceItem, InvoiceStatus, InvoiceType
from app.schemas.sales import (
    SalesInvoiceCreate, SalesInvoiceListResponse, SalesInvoiceResponse,
)
from app.services.sales_pdf import render_sales_invoice_pdf
from app.services.settings_service_client import SettingsServiceError, fetch_company_branding

logger = logging.getLogger(__name__)

# Its own router mounted at /invoices/sales. main.py includes this *before*
# the invoices router, so the literal "sales" path is matched before
# /invoices/{invoice_id} gets a chance to treat "sales" as an invoice id —
# the same ordering problem the scanner router already solves.
router = APIRouter(prefix="/invoices/sales", tags=["sales-invoices"])


def _compute_totals(payload: SalesInvoiceCreate) -> tuple[float, float, float]:
    """Subtotal/tax/total derived from the lines — never taken from the
    caller. See SalesInvoiceCreate's docstring for why."""
    subtotal = round(sum(item.amount for item in payload.items), 2)
    tax_amount = round(subtotal * payload.tax_rate, 2)
    return subtotal, tax_amount, round(subtotal + tax_amount, 2)


@router.post("/", response_model=SalesInvoiceResponse, status_code=201)
async def create_sales_invoice(
    payload: SalesInvoiceCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> SalesInvoiceResponse:
    subtotal, tax_amount, total = _compute_totals(payload)

    invoice = Invoice(
        company_id=company_id,
        type=InvoiceType.sale,
        # A generated invoice needs no review: its values were authored, not
        # inferred, so there is no extraction to second-guess. It starts where
        # a scanned invoice only arrives after a human has confirmed it.
        status=InvoiceStatus.validated,
        customer_name=payload.customer_name,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        ntn=payload.ntn,
        subtotal=subtotal,
        tax_rate=payload.tax_rate,
        tax_amount=tax_amount,
        total=total,
        # 1.0 — not a guess about a document, an exact record of what was
        # entered. The scan-only columns stay NULL (see migration 20260826_01).
        document_confidence=1.0,
        extraction_source="generated",
        review_flags=[],
    )
    invoice.items = [
        InvoiceItem(
            position=index,
            description=item.description,
            qty=item.qty,
            rate=item.rate,
            amount=item.amount,
            arithmetic_check="pass",  # computed here, so it is true by construction
            review_flags=[],
        )
        for index, item in enumerate(payload.items)
    ]

    db.add(invoice)
    await db.commit()
    await db.refresh(invoice, attribute_names=["items"])

    logger.info("Sales invoice created", extra={"invoice_id": str(invoice.id), "total": total})
    return SalesInvoiceResponse.model_validate(invoice)


@router.get("/", response_model=SalesInvoiceListResponse)
async def list_sales_invoices(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> SalesInvoiceListResponse:
    base = select(Invoice).where(Invoice.company_id == company_id, Invoice.type == InvoiceType.sale)
    total = await db.scalar(
        select(func.count()).select_from(Invoice)
        .where(Invoice.company_id == company_id, Invoice.type == InvoiceType.sale)
    ) or 0
    rows = (
        await db.execute(
            base.options(selectinload(Invoice.items))
            .order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
        )
    ).scalars().all()

    return SalesInvoiceListResponse(
        invoices=[SalesInvoiceResponse.model_validate(row) for row in rows],
        total=total, skip=skip, limit=limit,
    )


async def _get_sales_invoice(db: AsyncSession, invoice_id: UUID, company_id: UUID) -> Invoice:
    invoice = await db.scalar(
        select(Invoice)
        .where(
            Invoice.id == invoice_id,
            Invoice.company_id == company_id,
            # Scoped to sales: a purchase invoice has no customer and its
            # numbers came from OCR, so rendering it through this template
            # would present extracted guesses as an authored document.
            Invoice.type == InvoiceType.sale,
        )
        .options(selectinload(Invoice.items))
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Sales invoice not found")
    return invoice


@router.get("/{invoice_id}", response_model=SalesInvoiceResponse)
async def get_sales_invoice(
    invoice_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> SalesInvoiceResponse:
    return SalesInvoiceResponse.model_validate(await _get_sales_invoice(db, invoice_id, company_id))


@router.get("/{invoice_id}/pdf")
async def download_sales_invoice_pdf(
    invoice_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Renders the invoice to PDF on demand rather than storing one.

    A stored PDF would go stale the moment any field were corrected, and
    there would be no signal that it had — rendering per request means the
    document always matches the record it claims to represent.

    Branding (logo, placement, template) is read from Settings Service,
    best-effort: an unreachable Settings Service still produces a
    correct, plain-classic invoice rather than failing the download.
    """
    invoice = await _get_sales_invoice(db, invoice_id, company_id)

    company_name = None
    template = "classic"
    logo_data_uri = None
    logo_placement = "left"
    try:
        branding = await fetch_company_branding(settings, company_id)
        company_name = branding.get("name")
        template = branding.get("invoice_template") or template
        logo_data_uri = branding.get("logo_url")
        logo_placement = branding.get("logo_placement") or logo_placement
    except SettingsServiceError as exc:
        logger.warning("Could not fetch invoice branding for company %s: %s", company_id, exc)

    pdf_bytes = render_sales_invoice_pdf(
        invoice, company_name=company_name, template=template,
        logo_data_uri=logo_data_uri, logo_placement=logo_placement,
    )

    safe_number = (invoice.invoice_number or str(invoice.id)).replace('"', "'").replace("\r", "").replace("\n", "")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="invoice-{safe_number}.pdf"'},
    )
