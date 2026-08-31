from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    category: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=200)
    ntn: Optional[str] = Field(default=None, max_length=32)
    payment_terms: Optional[str] = Field(default=None, max_length=100)
    #: 0-5. None means unrated, which is different from rated zero.
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    status: Literal["active", "inactive", "review"] = "active"

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class VendorUpdate(BaseModel):
    """Every field optional; only those actually present are applied, so an
    omitted field is left alone while an explicit null clears it — the same
    model_fields_set convention used across this codebase."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    category: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=200)
    ntn: Optional[str] = Field(default=None, max_length=32)
    payment_terms: Optional[str] = Field(default=None, max_length=100)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    status: Optional[Literal["active", "inactive", "review"]] = None

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class VendorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    category: Optional[str] = None
    city: Optional[str] = None
    ntn: Optional[str] = None
    payment_terms: Optional[str] = None
    rating: Optional[float] = None
    status: str
    #: Derived from Invoice Service, never stored — see the model's docstring.
    #: 0.0 when this vendor has no linked invoices yet.
    total_spend_pkr: float = 0.0
    invoice_count: int = 0
    #: True when the spend figures above could not be fetched, so the caller
    #: can say "unavailable" rather than showing a real vendor's spend as
    #: zero — which would be a lie, not a gap.
    spend_unavailable: bool = False
    created_at: datetime
    updated_at: datetime


class VendorListResponse(BaseModel):
    vendors: list[VendorResponse]
    total: int
    skip: int
    limit: int


class VendorOption(BaseModel):
    """Suggested values the UI can offer. Deliberately not enforced — see
    the model's SUGGESTED_* constants for why."""

    categories: list[str]
    payment_terms: list[str]
