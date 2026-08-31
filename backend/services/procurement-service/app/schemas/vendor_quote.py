from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VendorQuoteCreate(BaseModel):
    purchase_request_id: UUID
    vendor_id: Optional[UUID] = None
    vendor_name: str = Field(min_length=1, max_length=500)
    price_pkr: float = Field(gt=0)
    delivery_estimate: Optional[str] = Field(default=None, max_length=100)
    quality_rating: Optional[str] = Field(default=None, max_length=50)
    payment_terms: Optional[str] = Field(default=None, max_length=100)
    #: A human judgement call, not computed — see the model's own docstring.
    score: Optional[int] = Field(default=None, ge=0, le=100)


class VendorQuoteUpdate(BaseModel):
    """Every field optional; only those actually present are applied —
    mainly for scoring a quote after the fact."""

    vendor_name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    price_pkr: Optional[float] = Field(default=None, gt=0)
    delivery_estimate: Optional[str] = Field(default=None, max_length=100)
    quality_rating: Optional[str] = Field(default=None, max_length=50)
    payment_terms: Optional[str] = Field(default=None, max_length=100)
    score: Optional[int] = Field(default=None, ge=0, le=100)


class VendorQuoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_request_id: UUID
    vendor_id: Optional[UUID] = None
    vendor_name: str
    price_pkr: float
    delivery_estimate: Optional[str] = None
    quality_rating: Optional[str] = None
    payment_terms: Optional[str] = None
    score: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class VendorQuoteListResponse(BaseModel):
    quotes: list[VendorQuoteResponse]
    total: int
