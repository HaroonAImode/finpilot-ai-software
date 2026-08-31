"""Purchase orders — architecture report §5.6's second half."""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import PurchaseOrder, PurchaseOrderStatus, PurchaseRequest, PurchaseRequestStatus
from app.schemas.purchase_order import (
    PurchaseOrderCreate, PurchaseOrderListResponse, PurchaseOrderResponse, PurchaseOrderStatusUpdate,
)

router = APIRouter(prefix="/procurement/orders", tags=["purchase-orders"])


def _to_response(order: PurchaseOrder) -> PurchaseOrderResponse:
    # Built field-by-field rather than PurchaseOrderResponse.model_validate
    # (order): `delayed` is computed, not a column, so from_attributes has
    # nothing to read it from — same reasoning as hr-service's employee
    # _to_response.
    return PurchaseOrderResponse(
        id=order.id, purchase_request_id=order.purchase_request_id,
        vendor_id=order.vendor_id, vendor_name=order.vendor_name,
        order_date=order.order_date, amount_pkr=order.amount_pkr, expected_delivery=order.expected_delivery,
        status=order.status.value,
        delayed=order.status == PurchaseOrderStatus.in_transit and order.expected_delivery < date.today(),
        created_at=order.created_at, updated_at=order.updated_at,
    )


async def _get_order_scoped(db: AsyncSession, order_id: UUID, company_id: UUID) -> PurchaseOrder:
    order = await db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == order_id, PurchaseOrder.company_id == company_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return order


@router.get("/", response_model=PurchaseOrderListResponse)
async def list_orders(
    status: Optional[str] = Query(None, description="in_transit, delivered or cancelled"),
    vendor_id: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderListResponse:
    query = select(PurchaseOrder).where(PurchaseOrder.company_id == company_id)
    count_query = select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.company_id == company_id)

    if status:
        try:
            resolved = PurchaseOrderStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown order status '{status}'")
        query = query.where(PurchaseOrder.status == resolved)
        count_query = count_query.where(PurchaseOrder.status == resolved)
    if vendor_id is not None:
        query = query.where(PurchaseOrder.vendor_id == vendor_id)
        count_query = count_query.where(PurchaseOrder.vendor_id == vendor_id)

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return PurchaseOrderListResponse(orders=[_to_response(r) for r in rows], total=total, skip=skip, limit=limit)


@router.post("/", response_model=PurchaseOrderResponse, status_code=201)
async def create_order(
    payload: PurchaseOrderCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    if payload.purchase_request_id is not None:
        request = await db.scalar(
            select(PurchaseRequest).where(
                PurchaseRequest.id == payload.purchase_request_id, PurchaseRequest.company_id == company_id,
            )
        )
        if request is None:
            raise HTTPException(status_code=404, detail="Purchase request not found")
        if request.status != PurchaseRequestStatus.approved:
            raise HTTPException(
                status_code=409, detail="An order can only be created from an approved purchase request",
            )

    order = PurchaseOrder(
        company_id=company_id, purchase_request_id=payload.purchase_request_id,
        vendor_id=payload.vendor_id, vendor_name=payload.vendor_name,
        order_date=payload.order_date or date.today(), amount_pkr=payload.amount_pkr,
        expected_delivery=payload.expected_delivery,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return _to_response(order)


@router.put("/{order_id}/status", response_model=PurchaseOrderResponse)
async def update_order_status(
    order_id: UUID, payload: PurchaseOrderStatusUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    order = await _get_order_scoped(db, order_id, company_id)
    try:
        order.status = PurchaseOrderStatus(payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown order status '{payload.status}'")
    await db.commit()
    await db.refresh(order)
    return _to_response(order)


@router.get("/{order_id}", response_model=PurchaseOrderResponse)
async def get_order(
    order_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    order = await _get_order_scoped(db, order_id, company_id)
    return _to_response(order)
