"""Vendor comparison — architecture report §5.6's third piece. Quotes are
recorded by a human, not solicited automatically — see VendorQuote's own
docstring for why there is no RFQ workflow here.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import VendorQuote
from app.schemas.vendor_quote import (
    VendorQuoteCreate, VendorQuoteListResponse, VendorQuoteResponse, VendorQuoteUpdate,
)

router = APIRouter(prefix="/procurement/vendor-comparison", tags=["vendor-comparison"])


async def _get_quote_scoped(db: AsyncSession, quote_id: UUID, company_id: UUID) -> VendorQuote:
    quote = await db.scalar(
        select(VendorQuote).where(VendorQuote.id == quote_id, VendorQuote.company_id == company_id)
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Vendor quote not found")
    return quote


@router.get("/", response_model=VendorQuoteListResponse)
async def list_quotes(
    purchase_request_id: Optional[UUID] = Query(
        None, description="Scope to one purchase request's quotes — omit for every quote this company has recorded.",
    ),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> VendorQuoteListResponse:
    query = select(VendorQuote).where(VendorQuote.company_id == company_id)
    if purchase_request_id is not None:
        query = query.where(VendorQuote.purchase_request_id == purchase_request_id)

    rows = (
        await db.execute(query.order_by(VendorQuote.score.desc().nullslast(), VendorQuote.price_pkr.asc()))
    ).scalars().all()

    return VendorQuoteListResponse(quotes=[VendorQuoteResponse.model_validate(r) for r in rows], total=len(rows))


@router.post("/", response_model=VendorQuoteResponse, status_code=201)
async def create_quote(
    payload: VendorQuoteCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> VendorQuoteResponse:
    quote = VendorQuote(
        company_id=company_id, purchase_request_id=payload.purchase_request_id,
        vendor_id=payload.vendor_id, vendor_name=payload.vendor_name, price_pkr=payload.price_pkr,
        delivery_estimate=payload.delivery_estimate, quality_rating=payload.quality_rating,
        payment_terms=payload.payment_terms, score=payload.score,
    )
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return VendorQuoteResponse.model_validate(quote)


@router.put("/{quote_id}", response_model=VendorQuoteResponse)
async def update_quote(
    quote_id: UUID, payload: VendorQuoteUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> VendorQuoteResponse:
    quote = await _get_quote_scoped(db, quote_id, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(quote, field_name, value)
    await db.commit()
    await db.refresh(quote)
    return VendorQuoteResponse.model_validate(quote)
