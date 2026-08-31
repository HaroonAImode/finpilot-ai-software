from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LOGO_PLACEMENT = Literal["left", "center", "right"]
INVOICE_TEMPLATE = Literal["classic", "modern", "midnight", "minimal"]


class CompanyUpdate(BaseModel):
    """Every field optional; only those actually present are applied —
    the same model_fields_set convention used across this codebase's
    other PUT endpoints."""

    name: Optional[str] = Field(default=None, max_length=300)
    ntn: Optional[str] = Field(default=None, max_length=32)
    address: Optional[str] = Field(default=None, max_length=500)
    city: Optional[str] = Field(default=None, max_length=200)
    #: A data: URI — no practical length cap beyond a sane upper bound
    #: against an oversized upload; the frontend itself constrains what
    #: it will ever send (see invoice-templates.tsx's own size check).
    logo_url: Optional[str] = Field(default=None, max_length=2_000_000)
    logo_placement: Optional[LOGO_PLACEMENT] = None
    invoice_template: Optional[INVOICE_TEMPLATE] = None
    industry: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    email: Optional[str] = Field(default=None, max_length=300)


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = None
    ntn: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    logo_url: Optional[str] = None
    logo_placement: str
    invoice_template: str
    industry: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
