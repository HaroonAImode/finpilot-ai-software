"""Vendor reconciliation — architecture report §5.7 / plan §3.8.

The accounting workflow that closes the loop the scanner opens: every scan
writes a raw `vendor_name`, never a `vendor_id`. This is where a human turns
that free text into a real, deduplicated vendor — one raw name at a time,
with the system's own best-guess matches, never auto-applied.

Own router (not folded into vendors.py) because it orchestrates across two
services — this one's vendor directory and Invoice Service's unlinked
invoices — and reads as a distinct workflow, not vendor CRUD.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import IgnoredVendorName, Vendor, VendorStatus
from app.schemas.reconciliation import (
    CreateAndLinkRequest, IgnoreRequest, IgnoredVendorNameResponse, LinkGroupRequest,
    ReconciliationActionResponse, ReconciliationGroup, ReconciliationQueueResponse,
)
from app.services.invoice_service_client import (
    InvoiceServiceError, bulk_link_vendor, fetch_vendor_groups,
)
from app.services.match import suggest_matches

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vendors/reconciliation", tags=["vendor-reconciliation"])


@router.get("/queue", response_model=ReconciliationQueueResponse)
async def reconciliation_queue(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReconciliationQueueResponse:
    """Every raw vendor_name still waiting on a decision, minus the ones
    already dismissed as not being a vendor, with this company's own
    best-guess matches attached to each.

    Fatal on a downstream failure (502), not degraded: the queue *is*
    Invoice Service's data, so an empty result here would tell the
    accountant "nothing to reconcile" when the truth is "could not ask" —
    the same reasoning as GET /vendors/{id}/invoices.
    """
    try:
        raw_groups = await fetch_vendor_groups(settings, company_id)
    except InvoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    ignored_names = set(
        (await db.scalars(
            select(IgnoredVendorName.vendor_name).where(IgnoredVendorName.company_id == company_id)
        )).all()
    )
    vendors = (await db.scalars(select(Vendor).where(Vendor.company_id == company_id))).all()

    groups = [
        ReconciliationGroup(**raw, suggestions=suggest_matches(raw["vendor_name"], list(vendors)))
        for raw in raw_groups
        if raw["vendor_name"] not in ignored_names
    ]
    return ReconciliationQueueResponse(groups=groups, ignored_count=len(ignored_names))


@router.post("/link", response_model=ReconciliationActionResponse)
async def link_group_to_existing_vendor(
    payload: LinkGroupRequest,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReconciliationActionResponse:
    """Links every invoice in one raw-name group to a vendor that already
    exists — the "yes, this is that vendor" action, whether the accountant
    picked a suggestion or searched the directory themselves."""
    vendor = await db.scalar(
        select(Vendor).where(Vendor.id == payload.vendor_id, Vendor.company_id == company_id)
    )
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    try:
        linked_count = await bulk_link_vendor(settings, company_id, payload.invoice_ids, payload.vendor_id)
    except InvoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info(
        "Reconciliation: linked group to existing vendor",
        extra={"vendor_name": payload.vendor_name, "vendor_id": str(payload.vendor_id), "count": linked_count},
    )
    return ReconciliationActionResponse(vendor_id=payload.vendor_id, linked_count=linked_count)


@router.post("/create-and-link", response_model=ReconciliationActionResponse, status_code=201)
async def create_vendor_and_link_group(
    payload: CreateAndLinkRequest,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReconciliationActionResponse:
    """The "this is a real vendor we've never recorded" action: creates the
    vendor and links the whole group in one step, rather than making the
    accountant do it as two separate trips through two different pages."""
    vendor = Vendor(
        company_id=company_id, name=payload.name, category=payload.category,
        city=payload.city, ntn=payload.ntn, payment_terms=payload.payment_terms,
        status=VendorStatus.active,
    )
    db.add(vendor)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f'A vendor named "{payload.name}" already exists')
    await db.refresh(vendor)

    try:
        linked_count = await bulk_link_vendor(settings, company_id, payload.invoice_ids, vendor.id)
    except InvoiceServiceError as exc:
        # The vendor now exists but nothing is linked to it yet — a real,
        # visible partial state rather than a rolled-back one, since the
        # vendor record itself is valid and worth keeping either way.
        raise HTTPException(
            status_code=502,
            detail=f"Vendor was created, but linking its invoices failed: {exc}",
        ) from exc

    logger.info(
        "Reconciliation: created vendor and linked group",
        extra={"vendor_name": payload.vendor_name, "vendor_id": str(vendor.id), "count": linked_count},
    )
    return ReconciliationActionResponse(vendor_id=vendor.id, linked_count=linked_count)


@router.post("/ignore", response_model=IgnoredVendorNameResponse, status_code=201)
async def ignore_vendor_name(
    payload: IgnoreRequest,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> IgnoredVendorNameResponse:
    """Marks one raw string as "not a vendor" — a scanner fallback like
    "Receipt" or a misread field, not a supplier. Keyed on the exact string,
    not a pattern: safer default, and reversible with one delete."""
    entry = IgnoredVendorName(company_id=company_id, vendor_name=payload.vendor_name)
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This name is already ignored")
    await db.refresh(entry)
    return IgnoredVendorNameResponse(id=entry.id, vendor_name=entry.vendor_name)


@router.get("/ignored", response_model=list[IgnoredVendorNameResponse])
async def list_ignored_vendor_names(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> list[IgnoredVendorNameResponse]:
    """So "ignore" is visibly reversible rather than a one-way door — an
    accountant can see and undo a mis-click."""
    rows = (
        await db.scalars(
            select(IgnoredVendorName)
            .where(IgnoredVendorName.company_id == company_id)
            .order_by(IgnoredVendorName.vendor_name)
        )
    ).all()
    return [IgnoredVendorNameResponse(id=row.id, vendor_name=row.vendor_name) for row in rows]


@router.delete("/ignored/{ignored_id}", status_code=204)
async def unignore_vendor_name(
    ignored_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    entry = await db.scalar(
        select(IgnoredVendorName).where(
            IgnoredVendorName.id == ignored_id, IgnoredVendorName.company_id == company_id,
        )
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(entry)
    await db.commit()
