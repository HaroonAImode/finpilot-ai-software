# Imported under an alias — the same reason transactions-service's
# schemas/expense.py does: `expected_delivery`/`order_date` avoid the
# collision by name here, but the alias is kept as a habit so a future
# field named `date` on this model never silently hits the confirmed
# Pydantic v2 gotcha (a field's own annotation resolving to NoneType when
# it shares its name with the imported type and carries a default).
from datetime import date as _date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PurchaseOrderCreate(BaseModel):
    #: Optional — most orders originate from an approved request
    #: (architecture report §5.6), but nothing forces every purchase
    #: through that flow. When present, must name an *approved* request in
    #: this company (checked in the route, not here).
    purchase_request_id: Optional[UUID] = None
    vendor_id: Optional[UUID] = None
    vendor_name: str = Field(min_length=1, max_length=500)
    order_date: Optional[_date] = None
    amount_pkr: float = Field(gt=0)
    expected_delivery: _date


class PurchaseOrderStatusUpdate(BaseModel):
    status: str


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_request_id: Optional[UUID] = None
    vendor_id: Optional[UUID] = None
    vendor_name: str
    order_date: _date
    amount_pkr: float
    expected_delivery: _date
    status: str
    #: True when status is still in_transit and expected_delivery has
    #: passed — computed on every read, not a stored status value. See
    #: PurchaseOrderStatus's own docstring for why.
    delayed: bool = False
    created_at: datetime
    updated_at: datetime


class PurchaseOrderListResponse(BaseModel):
    orders: list[PurchaseOrderResponse]
    total: int
    skip: int
    limit: int
