"""Vendors — architecture report §5.7.

Gives suppliers a stable identity. Before this service, `invoice.vendor_name`
was free text written from whatever OCR read, so a misread of one supplier
became a permanent second "vendor" — and the scanner's fuzzy matcher then
matched future invoices against that polluted list, compounding the error
with every scan.

Spend is **derived** from Invoice Service on every read, never stored here.
A cached aggregate would drift the moment an invoice is corrected in the
Records grid, and there is no event bus to keep it honest.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import SUGGESTED_CATEGORIES, SUGGESTED_PAYMENT_TERMS, Vendor, VendorStatus
from app.schemas.vendor import (
    VendorCreate, VendorListResponse, VendorOption, VendorResponse, VendorUpdate,
)
from app.services.invoice_service_client import (
    InvoiceServiceError, fetch_vendor_invoices, fetch_vendor_spend,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vendors", tags=["vendors"])


async def _get_vendor_scoped(db: AsyncSession, vendor_id: UUID, company_id: UUID) -> Vendor:
    vendor = await db.scalar(
        select(Vendor).where(Vendor.id == vendor_id, Vendor.company_id == company_id)
    )
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


async def _spend_map(settings: Settings, company_id: UUID) -> tuple[dict[UUID, tuple[float, int]], bool]:
    """Returns (spend by vendor, unavailable).

    A failure here degrades rather than fails the request: the vendor
    directory is still useful without spend, and a 502 for the whole list
    because one downstream aggregate is unavailable would be worse. The
    `unavailable` flag is what stops the UI from rendering a real vendor's
    spend as a confident 0.00.
    """
    try:
        return await fetch_vendor_spend(settings, company_id), False
    except InvoiceServiceError as exc:
        logger.warning("Vendor spend unavailable: %s", exc)
        return {}, True


def _to_response(
    vendor: Vendor, spend: dict[UUID, tuple[float, int]], unavailable: bool,
) -> VendorResponse:
    total, count = spend.get(vendor.id, (0.0, 0))
    response = VendorResponse.model_validate(vendor)
    response.total_spend_pkr = total
    response.invoice_count = count
    response.spend_unavailable = unavailable
    return response


@router.get("/options", response_model=VendorOption)
async def vendor_options() -> VendorOption:
    """Suggested categories and payment terms for the UI.

    Registered before /{vendor_id} so the literal path is not parsed as an
    id. Suggestions only — neither is enforced, see the model's own
    docstring for why a closed enum would be wrong here.
    """
    return VendorOption(
        categories=list(SUGGESTED_CATEGORIES), payment_terms=list(SUGGESTED_PAYMENT_TERMS),
    )


@router.get("/top", response_model=list[VendorResponse])
async def top_vendors(
    limit: int = Query(5, ge=1, le=50),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[VendorResponse]:
    """Top vendors by spend — §5.7's chart endpoint.

    Sorted here rather than in SQL because spend lives in another service;
    the vendor count per company is small enough that ordering in Python is
    not the bottleneck, and it keeps spend derived rather than duplicated.
    """
    spend, unavailable = await _spend_map(settings, company_id)
    vendors = (
        await db.execute(select(Vendor).where(Vendor.company_id == company_id))
    ).scalars().all()

    ranked = sorted(vendors, key=lambda v: spend.get(v.id, (0.0, 0))[0], reverse=True)
    return [_to_response(v, spend, unavailable) for v in ranked[:limit]]


@router.get("/", response_model=VendorListResponse)
async def list_vendors(
    status: Optional[str] = Query(None, description="Filter by status: active, inactive or review"),
    category: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive match on the vendor's name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VendorListResponse:
    query = select(Vendor).where(Vendor.company_id == company_id)
    count_query = select(func.count()).select_from(Vendor).where(Vendor.company_id == company_id)

    if status:
        try:
            resolved = VendorStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown vendor status '{status}'")
        query = query.where(Vendor.status == resolved)
        count_query = count_query.where(Vendor.status == resolved)
    if category:
        query = query.where(Vendor.category == category)
        count_query = count_query.where(Vendor.category == category)
    if city:
        query = query.where(Vendor.city == city)
        count_query = count_query.where(Vendor.city == city)
    if search:
        pattern = f"%{search}%"
        query = query.where(Vendor.name.ilike(pattern))
        count_query = count_query.where(Vendor.name.ilike(pattern))

    total = await db.scalar(count_query) or 0
    vendors = (
        await db.execute(query.order_by(Vendor.name).offset(skip).limit(limit))
    ).scalars().all()

    spend, unavailable = await _spend_map(settings, company_id)
    return VendorListResponse(
        vendors=[_to_response(v, spend, unavailable) for v in vendors],
        total=total, skip=skip, limit=limit,
    )


@router.post("/", response_model=VendorResponse, status_code=201)
async def create_vendor(
    payload: VendorCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> VendorResponse:
    vendor = Vendor(
        company_id=company_id, name=payload.name, category=payload.category, city=payload.city,
        ntn=payload.ntn, payment_terms=payload.payment_terms, rating=payload.rating,
        status=VendorStatus(payload.status),
    )
    db.add(vendor)
    try:
        await db.commit()
    except IntegrityError:
        # The per-company unique name constraint. Reported as a clear 409
        # rather than a 500: a duplicate vendor is precisely the thing this
        # service exists to prevent, so the caller should be told plainly.
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f'A vendor named "{payload.name}" already exists',
        )
    await db.refresh(vendor)

    # Newly created, so it has no linked invoices yet — no need to ask
    # Invoice Service for a spend figure that is definitionally zero.
    return _to_response(vendor, {}, False)


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VendorResponse:
    vendor = await _get_vendor_scoped(db, vendor_id, company_id)
    spend, unavailable = await _spend_map(settings, company_id)
    return _to_response(vendor, spend, unavailable)


@router.put("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(
    vendor_id: UUID, payload: VendorUpdate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VendorResponse:
    vendor = await _get_vendor_scoped(db, vendor_id, company_id)

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "status" and value is not None:
            value = VendorStatus(value)
        setattr(vendor, field_name, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Another vendor in this company already has that name",
        )
    await db.refresh(vendor)

    spend, unavailable = await _spend_map(settings, company_id)
    return _to_response(vendor, spend, unavailable)


@router.get("/{vendor_id}/invoices")
async def vendor_invoices(
    vendor_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Every invoice linked to this vendor.

    Unlike spend on the list endpoint, a failure here **is** fatal (502):
    the entire response is the downstream data, so degrading would mean
    returning an empty list that reads as "this vendor has no invoices" —
    a factual claim we cannot make when we simply could not ask.
    """
    # Scoped first, so another company's vendor id 404s here rather than
    # reaching Invoice Service at all.
    await _get_vendor_scoped(db, vendor_id, company_id)

    try:
        return await fetch_vendor_invoices(
            settings, company_id, vendor_id, skip=skip, limit=limit,
        )
    except InvoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
