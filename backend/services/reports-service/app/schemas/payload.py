"""The one generic shape every report type produces — see the design
spec §5. A single pair of renderers (pdf.py, excel.py) consumes this
shape regardless of report type, rather than one bespoke renderer per
report; a report type only needs to know how to become one of these, not
how to lay out a page.
"""
from typing import Optional

from pydantic import BaseModel


class SummaryLine(BaseModel):
    label: str
    #: Pre-formatted for display (e.g. "PKR 1,234,567", "18%") — the
    #: renderers print this verbatim rather than re-formatting a raw
    #: number, so every report controls its own precision/currency once,
    #: in the compute function that builds it.
    value: str


class ReportTable(BaseModel):
    headers: list[str]
    rows: list[list[str]]


class ReportPayload(BaseModel):
    title: str
    company_name: Optional[str] = None
    #: e.g. "01 Jul 2026 – 31 Jul 2026" or "As of 31 Jul 2026".
    period_label: str
    summary: list[SummaryLine]
    table: Optional[ReportTable] = None
    #: Disclaimers and known-limitation notes — e.g. Balance Sheet's own
    #: "this is a cash-position snapshot, not a full balance sheet."
    #: Rendered on both the PDF and the Excel, never hidden.
    notes: list[str] = []
