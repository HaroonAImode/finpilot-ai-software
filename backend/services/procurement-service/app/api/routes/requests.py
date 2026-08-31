"""Purchase requests — architecture report §5.6's first half. `/{request_id}
/timeline` is **derived** from this request's own lifecycle plus any
PurchaseOrder created from it, not a separately-authored steps table: a
freeform timeline that could drift from what actually happened would be
worse than fewer, always-accurate steps. See the design spec §6.1.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import (
    SUGGESTED_DEPARTMENTS, PurchaseOrder, PurchaseRequest, PurchaseRequestStatus,
)
from app.schemas.purchase_request import (
    PurchaseRequestCreate, PurchaseRequestListResponse, PurchaseRequestOptions,
    PurchaseRequestResponse, PurchaseRequestTimeline, TimelineStep,
)

router = APIRouter(prefix="/procurement/requests", tags=["purchase-requests"])


async def _get_request_scoped(db: AsyncSession, request_id: UUID, company_id: UUID) -> PurchaseRequest:
    request = await db.scalar(
        select(PurchaseRequest).where(PurchaseRequest.id == request_id, PurchaseRequest.company_id == company_id)
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    return request


@router.get("/options", response_model=PurchaseRequestOptions)
async def request_options() -> PurchaseRequestOptions:
    """Suggested departments. Registered before /{request_id} so the
    literal path is not parsed as an id."""
    return PurchaseRequestOptions(departments=list(SUGGESTED_DEPARTMENTS))


@router.get("/", response_model=PurchaseRequestListResponse)
async def list_requests(
    status: Optional[str] = Query(None, description="pending_approval, approved or rejected"),
    department: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive match on item_description"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestListResponse:
    query = select(PurchaseRequest).where(PurchaseRequest.company_id == company_id)
    count_query = select(func.count()).select_from(PurchaseRequest).where(PurchaseRequest.company_id == company_id)

    if status:
        try:
            resolved = PurchaseRequestStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown request status '{status}'")
        query = query.where(PurchaseRequest.status == resolved)
        count_query = count_query.where(PurchaseRequest.status == resolved)
    if department:
        query = query.where(PurchaseRequest.department == department)
        count_query = count_query.where(PurchaseRequest.department == department)
    if search:
        pattern = f"%{search}%"
        query = query.where(PurchaseRequest.item_description.ilike(pattern))
        count_query = count_query.where(PurchaseRequest.item_description.ilike(pattern))

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.order_by(PurchaseRequest.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return PurchaseRequestListResponse(
        requests=[PurchaseRequestResponse.model_validate(r) for r in rows], total=total, skip=skip, limit=limit,
    )


@router.post("/", response_model=PurchaseRequestResponse, status_code=201)
async def create_request(
    payload: PurchaseRequestCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    request = PurchaseRequest(
        company_id=company_id, item_description=payload.item_description, department=payload.department,
        requester_name=payload.requester_name, amount_pkr=payload.amount_pkr,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return PurchaseRequestResponse.model_validate(request)


@router.get("/{request_id}", response_model=PurchaseRequestResponse)
async def get_request(
    request_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    request = await _get_request_scoped(db, request_id, company_id)
    return PurchaseRequestResponse.model_validate(request)


@router.post("/{request_id}/approve", response_model=PurchaseRequestResponse)
async def approve_request(
    request_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    request = await _get_request_scoped(db, request_id, company_id)
    if request.status != PurchaseRequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Only a request awaiting approval can be approved")
    request.status = PurchaseRequestStatus.approved
    await db.commit()
    await db.refresh(request)
    return PurchaseRequestResponse.model_validate(request)


@router.post("/{request_id}/reject", response_model=PurchaseRequestResponse)
async def reject_request(
    request_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    request = await _get_request_scoped(db, request_id, company_id)
    if request.status != PurchaseRequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Only a request awaiting approval can be rejected")
    request.status = PurchaseRequestStatus.rejected
    await db.commit()
    await db.refresh(request)
    return PurchaseRequestResponse.model_validate(request)


@router.get("/{request_id}/timeline", response_model=PurchaseRequestTimeline)
async def request_timeline(
    request_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PurchaseRequestTimeline:
    request = await _get_request_scoped(db, request_id, company_id)

    decided = request.status != PurchaseRequestStatus.pending_approval
    decision_detail = {
        PurchaseRequestStatus.approved: "Approved",
        PurchaseRequestStatus.rejected: "Rejected",
        PurchaseRequestStatus.pending_approval: "Awaiting approval",
    }[request.status]

    steps = [
        TimelineStep(
            title="Request submitted", detail=f"{request.department} · {request.requester_name}",
            date=request.created_at.isoformat(), completed=True,
        ),
        TimelineStep(
            title="Approval decision", detail=decision_detail,
            date=request.updated_at.isoformat() if decided else None, completed=decided,
        ),
    ]

    # At most one order is expected per request in the common flow, but
    # nothing in the schema enforces that — the earliest one is what the
    # timeline follows, same as "first thing that happened" everywhere else.
    order = await db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.company_id == company_id, PurchaseOrder.purchase_request_id == request_id)
        .order_by(PurchaseOrder.created_at.asc())
    )
    if order is not None:
        steps.append(
            TimelineStep(
                title="Purchase order issued", detail=order.vendor_name,
                date=order.created_at.isoformat(), completed=True,
            )
        )
        delivered = order.status.value == "delivered"
        steps.append(
            TimelineStep(
                title="Delivered", detail=order.vendor_name if delivered else "Not yet delivered",
                date=order.updated_at.isoformat() if delivered else None, completed=delivered,
            )
        )
    else:
        steps.append(TimelineStep(title="Purchase order issued", detail="Not yet issued", date=None, completed=False))

    return PurchaseRequestTimeline(request_id=request_id, steps=steps)
