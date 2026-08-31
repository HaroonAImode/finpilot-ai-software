"""render_category_report_pdf — byte-signature smoke tests, same reasoning
as test_sales_pdf.py: both PDF and PNG are real, well-defined binary
formats, so "a valid PDF came out, and it grew when an appendix image was
added" is a cheap, honest proxy for "this didn't crash and produced
something real" without asserting on internal layout.
"""
import base64
import uuid
from datetime import date

from app.models import Invoice, InvoiceStatus, InvoiceType, PaymentMethod
from app.services.category_report_pdf import render_category_report_pdf

PNG_1X1_BASE64_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_1X1_DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_1X1_BASE64_BYTES).decode()


def _invoice(**overrides) -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(), company_id=uuid.uuid4(), type=InvoiceType.purchase, status=InvoiceStatus.validated,
        vendor_name="KFC", invoice_number=None, invoice_date=date(2026, 6, 2), category="Office Entertainment",
        total=20_050.0, payment_method=PaymentMethod.cash, document_confidence=1.0, extraction_source="ocr",
        review_flags=[], raw_extraction_json={}, filename="kfc-receipt.jpg", size=100, s3_key="k/kfc.jpg",
        file_hash=uuid.uuid4().hex,
    )
    for key, value in overrides.items():
        setattr(invoice, key, value)
    return invoice


def test_a_category_with_entries_produces_a_real_pdf() -> None:
    invoices = [_invoice(), _invoice(vendor_name="Papa Johns", total=20_420.0)]
    pdf_bytes = render_category_report_pdf(
        category_label="Office Entertainment", invoices=invoices, entry_images={}, category_total=40_470.0,
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_an_empty_category_still_produces_a_valid_pdf() -> None:
    pdf_bytes = render_category_report_pdf(
        category_label="Legal & Professional", invoices=[], entry_images={}, category_total=0.0,
    )
    assert pdf_bytes.startswith(b"%PDF-")


def test_appendix_images_grow_the_document() -> None:
    invoice = _invoice()
    without_appendix = render_category_report_pdf(
        category_label="Office Entertainment", invoices=[invoice], entry_images={}, category_total=20_050.0,
    )
    with_appendix = render_category_report_pdf(
        category_label="Office Entertainment", invoices=[invoice],
        entry_images={invoice.id: PNG_1X1_BASE64_BYTES}, category_total=20_050.0,
    )
    assert with_appendix.startswith(b"%PDF-")
    assert len(with_appendix) > len(without_appendix)


def test_an_entry_with_no_particulars_falls_back_gracefully() -> None:
    invoice = _invoice(vendor_name=None, invoice_number=None, filename=None)
    pdf_bytes = render_category_report_pdf(
        category_label="Other", invoices=[invoice], entry_images={}, category_total=20_050.0,
    )
    assert pdf_bytes.startswith(b"%PDF-")


def test_a_company_logo_grows_the_document_and_still_produces_a_valid_pdf() -> None:
    without_logo = render_category_report_pdf(
        category_label="Office Entertainment", invoices=[_invoice()], entry_images={}, category_total=20_050.0,
    )
    with_logo = render_category_report_pdf(
        category_label="Office Entertainment", invoices=[_invoice()], entry_images={}, category_total=20_050.0,
        company_name="STIXOR Technologies", logo_data_uri=PNG_1X1_DATA_URI,
    )
    assert with_logo.startswith(b"%PDF-")
    assert len(with_logo) > len(without_logo)


def test_a_malformed_logo_data_uri_is_ignored_not_fatal() -> None:
    pdf_bytes = render_category_report_pdf(
        category_label="Office Entertainment", invoices=[_invoice()], entry_images={}, category_total=20_050.0,
        logo_data_uri="not-a-data-uri-at-all",
    )
    assert pdf_bytes.startswith(b"%PDF-")
