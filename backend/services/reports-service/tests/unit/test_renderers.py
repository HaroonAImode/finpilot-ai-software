"""PDF/Excel rendering — smoke tests only: both formats have real,
well-defined binary signatures, so a byte-level check is a cheap, honest
proxy for "a real file was produced" without asserting on internal layout
that would make this brittle for no real benefit.
"""
from app.schemas.payload import ReportPayload, ReportTable, SummaryLine
from app.services.excel_renderer import render_excel
from app.services.pdf_renderer import render_pdf

PAYLOAD = ReportPayload(
    title="Profit & Loss Statement",
    company_name="Khan Enterprises",
    period_label="01 Jul 2026 – 31 Jul 2026",
    summary=[SummaryLine(label="Revenue", value="PKR 500,000"), SummaryLine(label="Net Profit", value="PKR 300,000")],
    table=ReportTable(headers=["Category", "Amount"], rows=[["Raw Material", "PKR 150,000"]]),
    notes=["A simplified estimate."],
)

NO_TABLE_PAYLOAD = ReportPayload(
    title="Balance Sheet", period_label="As of 31 Jul 2026",
    summary=[SummaryLine(label="Cash Balance (approx.)", value="PKR 300,000")],
)


class TestPdf:
    def test_produces_a_real_pdf(self) -> None:
        pdf_bytes = render_pdf(PAYLOAD)
        assert pdf_bytes.startswith(b"%PDF-")
        assert len(pdf_bytes) > 500

    def test_works_with_no_table_and_no_company_name(self) -> None:
        pdf_bytes = render_pdf(NO_TABLE_PAYLOAD)
        assert pdf_bytes.startswith(b"%PDF-")


class TestExcel:
    def test_produces_a_real_xlsx(self) -> None:
        excel_bytes = render_excel(PAYLOAD)
        # .xlsx is a zip archive — "PK\x03\x04" is the local-file-header signature.
        assert excel_bytes.startswith(b"PK\x03\x04")
        assert len(excel_bytes) > 500

    def test_works_with_no_table_and_no_company_name(self) -> None:
        excel_bytes = render_excel(NO_TABLE_PAYLOAD)
        assert excel_bytes.startswith(b"PK\x03\x04")
