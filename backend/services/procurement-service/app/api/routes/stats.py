"""Dashboard stats — architecture report §5.6's `/procurement/stats`.
Registered under the bare `/procurement` prefix, distinct from
`/procurement/requests` and `/procurement/orders`, so there is no
route-ordering hazard with either of those routers' own `/{id}` patterns.
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import PurchaseOrder, PurchaseOrderStatus, PurchaseRequest, PurchaseRequestStatus
from app.schemas.stats import ProcurementStats

router = APIRouter(prefix="/procurement", tags=["procurement-stats"])


@router.get("/stats", response_model=ProcurementStats)
async def procurement_stats(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ProcurementStats:
    pending_count, pending_amount = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(PurchaseRequest.amount_pkr), 0.0))
            .where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequest.status == PurchaseRequestStatus.pending_approval,
            )
        )
    ).one()

    completed_count = await db.scalar(
        select(func.count()).where(
            PurchaseOrder.company_id == company_id, PurchaseOrder.status == PurchaseOrderStatus.delivered,
        )
    ) or 0

    cancelled_count = await db.scalar(
        select(func.count()).where(
            PurchaseOrder.company_id == company_id, PurchaseOrder.status == PurchaseOrderStatus.cancelled,
        )
    ) or 0

    # "Delayed" is computed, not a stored status — an in-transit order
    # whose expected_delivery has already passed. See
    # PurchaseOrderStatus's own docstring.
    delayed_count = await db.scalar(
        select(func.count()).where(
            PurchaseOrder.company_id == company_id, PurchaseOrder.status == PurchaseOrderStatus.in_transit,
            PurchaseOrder.expected_delivery < date.today(),
        )
    ) or 0

    return ProcurementStats(
        pending_count=pending_count, pending_amount_pkr=round(pending_amount or 0.0, 2),
        completed_count=completed_count, delayed_count=delayed_count, cancelled_count=cancelled_count,
    )
