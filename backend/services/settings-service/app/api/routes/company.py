"""Company profile — architecture report §5.10. Deliberately separate
from Auth Service's own read-only `company_name`; see Company's own
docstring for why the two are not synchronised.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Company, InvoiceTemplate, LogoPlacement
from app.schemas.company import CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/settings/company", tags=["settings-company"])


async def _get_or_create(db: AsyncSession, company_id: UUID) -> Company:
    """Nothing in Auth Service provisions a row here at signup, so the
    first read or write for a company creates one with defaults — the
    same lazy-singleton pattern used for AutomationSettings and
    TaxSettings below."""
    company = await db.scalar(select(Company).where(Company.company_id == company_id))
    if company is None:
        company = Company(company_id=company_id)
        db.add(company)
        await db.commit()
        await db.refresh(company)
    return company


@router.get("", response_model=CompanyResponse)
@router.get("/", response_model=CompanyResponse)
async def get_company(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    return CompanyResponse.model_validate(await _get_or_create(db, company_id))


@router.put("", response_model=CompanyResponse)
@router.put("/", response_model=CompanyResponse)
async def update_company(
    payload: CompanyUpdate, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    company = await _get_or_create(db, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "logo_placement" and value is not None:
            value = LogoPlacement(value)
        elif field_name == "invoice_template" and value is not None:
            value = InvoiceTemplate(value)
        setattr(company, field_name, value)
    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)
