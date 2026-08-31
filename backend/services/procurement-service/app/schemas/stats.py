from pydantic import BaseModel


class ProcurementStats(BaseModel):
    """The Procurement page's 4 stat cards.

    `pending` is purchase requests still awaiting a decision (not orders —
    an approved-and-placed order is no longer "pending," it's "in
    transit," which has no card of its own in v1). `completed` and
    `cancelled` are purchase order counts. `delayed` is computed, not a
    stored status — see PurchaseOrderStatus's own docstring.
    """

    pending_count: int
    pending_amount_pkr: float
    completed_count: int
    delayed_count: int
    cancelled_count: int
