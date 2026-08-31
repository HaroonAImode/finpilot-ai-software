"""AI automation toggles — architecture report §5.10. See
AutomationSettings' own docstring for exactly which of these gate real
pipeline behaviour today and which are stored-but-not-yet-enforced.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import AutomationSettings
from app.schemas.automation_settings import AutomationSettingsResponse, AutomationSettingsUpdate

router = APIRouter(prefix="/settings/automation", tags=["settings-automation"])


async def _get_or_create(db: AsyncSession, company_id: UUID) -> AutomationSettings:
    settings = await db.scalar(select(AutomationSettings).where(AutomationSettings.company_id == company_id))
    if settings is None:
        settings = AutomationSettings(company_id=company_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.get("", response_model=AutomationSettingsResponse)
@router.get("/", response_model=AutomationSettingsResponse)
async def get_automation_settings(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> AutomationSettingsResponse:
    return AutomationSettingsResponse.model_validate(await _get_or_create(db, company_id))


@router.put("", response_model=AutomationSettingsResponse)
@router.put("/", response_model=AutomationSettingsResponse)
async def update_automation_settings(
    payload: AutomationSettingsUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> AutomationSettingsResponse:
    settings = await _get_or_create(db, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field_name, value)
    await db.commit()
    await db.refresh(settings)
    return AutomationSettingsResponse.model_validate(settings)
