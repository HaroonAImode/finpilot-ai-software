"""Tax configuration — architecture report §5.10's "default 18% GST for
Pakistan."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import FilerStatus, TaxSettings
from app.schemas.tax_settings import TaxSettingsResponse, TaxSettingsUpdate

router = APIRouter(prefix="/settings/tax", tags=["settings-tax"])


async def _get_or_create(db: AsyncSession, company_id: UUID) -> TaxSettings:
    settings = await db.scalar(select(TaxSettings).where(TaxSettings.company_id == company_id))
    if settings is None:
        settings = TaxSettings(company_id=company_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.get("", response_model=TaxSettingsResponse)
@router.get("/", response_model=TaxSettingsResponse)
async def get_tax_settings(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> TaxSettingsResponse:
    return TaxSettingsResponse.model_validate(await _get_or_create(db, company_id))


@router.put("", response_model=TaxSettingsResponse)
@router.put("/", response_model=TaxSettingsResponse)
async def update_tax_settings(
    payload: TaxSettingsUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> TaxSettingsResponse:
    settings = await _get_or_create(db, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "filer_status" and value is not None:
            value = FilerStatus(value)
        setattr(settings, field_name, value)
    await db.commit()
    await db.refresh(settings)
    return TaxSettingsResponse.model_validate(settings)
