from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PurchaseRequestCreate(BaseModel):
    item_description: str = Field(min_length=1, max_length=500)
    department: str = Field(min_length=1, max_length=100)
    requester_name: str = Field(min_length=1, max_length=300)
    amount_pkr: float = Field(gt=0)

    @field_validator("item_description", "department", "requester_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cannot be blank")
        return cleaned


class PurchaseRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    item_description: str
    department: str
    requester_name: str
    amount_pkr: float
    status: str
    created_at: datetime
    updated_at: datetime


class PurchaseRequestListResponse(BaseModel):
    requests: list[PurchaseRequestResponse]
    total: int
    skip: int
    limit: int


class PurchaseRequestOptions(BaseModel):
    departments: list[str]


class TimelineStep(BaseModel):
    title: str
    detail: str
    #: ISO date/datetime, or None while this step hasn't happened yet.
    date: Optional[str] = None
    completed: bool


class PurchaseRequestTimeline(BaseModel):
    request_id: UUID
    steps: list[TimelineStep]
