"""render_sales_invoice_pdf — all four templates, all three logo
placements, and the no-logo/no-branding default path. Byte-signature
smoke tests only: both are real, well-defined binary formats, so "a
valid PDF came out" is a cheap, honest proxy for "this didn't crash and
produced something real" without asserting on internal layout that would
make this brittle for no benefit.
"""
import uuid
from datetime import date

import pytest

from app.models import Invoice, InvoiceItem, InvoiceStatus, InvoiceType
from app.services.sales_pdf import render_sales_invoice_pdf

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _invoice(**overrides) -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(), company_id=uuid.uuid4(), type=InvoiceType.sale, status=InvoiceStatus.processed,
        customer_name="Al-Madina Retail", invoice_number="INV-2026-0184", invoice_date=date(2026, 8, 4),
        ntn="4820193-6", subtotal=100_000.0, tax_rate=0.18, tax_amount=18_000.0, total=118_000.0,
        document_confidence=1.0, extraction_source="generated", review_flags=[], raw_extraction_json={},
        filename="", size=0, s3_key="", file_hash=uuid.uuid4().hex,
    )
    invoice.items = [
        InvoiceItem(position=0, description="Steel Sheets", qty=2, rate=9800, amount=19600, arithmetic_check="pass", review_flags=[]),
    ]
    for key, value in overrides.items():
        setattr(invoice, key, value)
    return invoice


@pytest.mark.parametrize("template", ["classic", "modern", "midnight", "minimal"])
def test_every_template_produces_a_real_pdf(template) -> None:
    pdf_bytes = render_sales_invoice_pdf(_invoice(), template=template)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


@pytest.mark.parametrize("placement", ["left", "center", "right"])
def test_every_logo_placement_produces_a_real_pdf(placement) -> None:
    pdf_bytes = render_sales_invoice_pdf(
        _invoice(), logo_data_uri=f"data:image/png;base64,{PNG_1X1_BASE64}", logo_placement=placement,
    )
    assert pdf_bytes.startswith(b"%PDF-")


def test_an_unknown_template_falls_back_to_classic() -> None:
    pdf_bytes = render_sales_invoice_pdf(_invoice(), template="not-a-real-template")
    assert pdf_bytes.startswith(b"%PDF-")


def test_a_malformed_logo_data_uri_is_ignored_not_fatal() -> None:
    pdf_bytes = render_sales_invoice_pdf(_invoice(), logo_data_uri="not-a-data-uri-at-all")
    assert pdf_bytes.startswith(b"%PDF-")


def test_no_branding_at_all_still_renders() -> None:
    pdf_bytes = render_sales_invoice_pdf(_invoice())
    assert pdf_bytes.startswith(b"%PDF-")
