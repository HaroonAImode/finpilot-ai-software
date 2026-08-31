from datetime import date as _date

from pydantic import BaseModel, model_validator


class ReportPeriodRequest(BaseModel):
    """Profit & Loss, Cash Flow, Tax Summary, Sales, and Purchases are all
    period-range reports — architecture report §5.9's own wording ("P&L
    for a period")."""

    period_start: _date
    period_end: _date

    @model_validator(mode="after")
    def _start_before_end(self) -> "ReportPeriodRequest":
        if self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        return self


class BalanceSheetRequest(BaseModel):
    """Balance Sheet is a point-in-time snapshot — architecture report
    §5.9's own wording ("Balance Sheet as of a date"), not a range."""

    as_of_date: _date
