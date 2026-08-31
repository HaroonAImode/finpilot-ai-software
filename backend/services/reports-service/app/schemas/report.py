from datetime import date as _date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.payload import ReportPayload


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    period_start: _date
    period_end: _date
    status: str
    payload: ReportPayload
    generated_at: datetime
    created_at: datetime


class ReportListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    period_start: _date
    period_end: _date
    status: str
    generated_at: datetime


class ReportListResponse(BaseModel):
    reports: list[ReportListItem]
    total: int
    skip: int
    limit: int
