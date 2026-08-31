"""Invoice Generator — the *sales* half of architecture report §5.3.

Distinct from the scanner's schemas in one important way: nothing here is
extracted, so nothing carries a confidence. A sales invoice's numbers are
authored by the user, and the service's job is to record and total them
faithfully, not to guess at them.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SalesLineItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    qty: float = Field(gt=0, description="Must be positive — a zero-quantity line is not a line")
    rate: float = Field(ge=0, description="Unit price; 0 is allowed for a free/bundled line")

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("description cannot be blank")
        return cleaned

    @property
    def amount(self) -> float:
        return round(self.qty * self.rate, 2)


class SalesInvoiceCreate(BaseModel):
    """What the caller supplies. Totals are deliberately **not** accepted —
    they are computed from the line items and the tax rate.

    Taking a client-supplied total would let the stored subtotal, tax and
    total disagree with the lines they are supposed to summarise, which is
    exactly the inconsistency the scanner's own arithmetic validation
    (Rule 5.1) exists to catch on the purchase side.
    """

    customer_name: str = Field(min_length=1, max_length=500)
    invoice_number: Optional[str] = Field(default=None, max_length=128)
    invoice_date: Optional[date] = None
    ntn: Optional[str] = Field(default=None, max_length=32)
    #: Fractional, not a percentage — 0.17 means 17%, matching how the
    #: scanner stores tax_rate so both halves of the table agree.
    tax_rate: float = Field(default=0.0, ge=0, le=1)
    items: list[SalesLineItemCreate] = Field(min_length=1)

    @field_validator("customer_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("customer_name cannot be blank")
        return cleaned


class SalesLineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    description: Optional[str] = None
    qty: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None


class SalesInvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    ntn: Optional[str] = None
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    items: list[SalesLineItemResponse] = []


class SalesInvoiceListResponse(BaseModel):
    invoices: list[SalesInvoiceResponse]
    total: int
    skip: int
    limit: int
