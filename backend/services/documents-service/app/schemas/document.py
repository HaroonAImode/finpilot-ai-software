from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """One stored document, in the shape the Documents page's
    `UnifiedDocument` already expects from every other source."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    mimetype: Optional[str] = None
    size: int
    category: str
    scanner_status: str
    invoice_id: Optional[UUID] = None
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    skip: int
    limit: int


class CategoryUpdate(BaseModel):
    #: Constrained to the same vocabulary the connectors use, so a typo can
    #: never create a category the Documents page has no filter for.
    category: Literal["invoices", "receipts", "reports", "contracts", "images", "other"]


class SendToScannerResponse(BaseModel):
    """The result of handing a document to Invoice Service's scanner."""

    document_id: UUID
    invoice_id: Optional[UUID] = None
    scanner_status: str
    message: str
