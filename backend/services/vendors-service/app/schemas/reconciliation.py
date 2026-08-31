"""Vendor reconciliation — architecture report §5.7 / plan §3.8.

Every purchase invoice arrives with a `vendor_name` written by OCR, not a
`vendor_id`. Left alone, that free text does exactly what it did before this
service existed: a misread of "ABC Traders" becomes a second permanent
"vendor", and every report and spend total downstream silently drifts.

This is the fix — an accountant's queue, one raw name at a time, with the
system's own best guess at which existing vendor (if any) it already is.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class VendorMatchSuggestion(BaseModel):
    """One candidate existing vendor for a raw name, ranked by string
    similarity. A suggestion, never auto-applied — see match.py's docstring
    for why a human decides every link."""

    vendor_id: UUID
    name: str
    score: float


class ReconciliationGroup(BaseModel):
    """Every unlinked invoice sharing one raw vendor_name, plus this
    company's best-guess matches against its own vendor directory."""

    vendor_name: str
    invoice_ids: list[UUID]
    invoice_count: int
    total_amount: float
    sample_invoice_number: Optional[str] = None
    latest_invoice_date: Optional[date] = None
    suggestions: list[VendorMatchSuggestion] = []


class ReconciliationQueueResponse(BaseModel):
    groups: list[ReconciliationGroup]
    #: Distinct raw names an accountant has already dismissed as not being
    #: a vendor — surfaced so the queue's size isn't a mystery ("why did 9
    #: names become 6") and so ignoring one is visibly reversible.
    ignored_count: int


class LinkGroupRequest(BaseModel):
    vendor_name: str = Field(min_length=1)
    invoice_ids: list[UUID] = Field(min_length=1)
    vendor_id: UUID


class CreateAndLinkRequest(BaseModel):
    """Same shape as VendorCreate, plus the group being resolved — creating
    the vendor and linking the group is one accountant action, not two."""

    vendor_name: str = Field(min_length=1)
    invoice_ids: list[UUID] = Field(min_length=1)

    name: str = Field(min_length=1, max_length=500)
    category: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=200)
    ntn: Optional[str] = Field(default=None, max_length=32)
    payment_terms: Optional[str] = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class ReconciliationActionResponse(BaseModel):
    vendor_id: UUID
    linked_count: int


class IgnoreRequest(BaseModel):
    vendor_name: str = Field(min_length=1)


class IgnoredVendorNameResponse(BaseModel):
    id: UUID
    vendor_name: str
