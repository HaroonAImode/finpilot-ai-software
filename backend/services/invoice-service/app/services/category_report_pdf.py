"""Renders a Saved Records category — its entries table plus each entry's
actual receipt/invoice image as a labeled appendix — to a single PDF.

Same ReportLab approach and the same `ImageReader`-for-measurement-only /
`Image(BytesIO(...))` pattern as sales_pdf.py (see that module's docstring
for why ReportLab over a HTML-to-PDF engine, and for the concrete TypeError
that pattern avoids). Deliberately its own module rather than extending
sales_pdf.py: a category report has no invoice-template concept (classic/
modern/midnight/minimal) — it is one plain, professional layout — and no
line-item table; reusing that renderer would mean threading a pile of
category-report-only parameters through a function built for a different
document entirely.

See docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md §5.
"""
import base64
import logging
from datetime import date as date_type, datetime
from io import BytesIO
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Invoice

logger = logging.getLogger(__name__)

_PAGE_SIZE = A4
_ACCENT = colors.HexColor("#0F766E")
_HEADING = colors.HexColor("#0F766E")
_TEXT = colors.HexColor("#111827")
_MUTED = colors.HexColor("#6B7280")
_RULE = colors.HexColor("#E5E7EB")
_ZEBRA = colors.HexColor("#F9FAFB")
_HEADER_TEXT = colors.white

_MAX_APPENDIX_WIDTH = 170 * mm
_MAX_APPENDIX_HEIGHT = 200 * mm
_MAX_LOGO_HEIGHT = 18 * mm
_MAX_LOGO_WIDTH = 60 * mm


def _money(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}"


def _decode_logo(logo_data_uri: str | None) -> bytes | None:
    """Same contract as sales_pdf.py's own `_decode_logo` — a
    `data:image/...;base64,...` string, decoded to raw bytes, or None for
    anything that doesn't parse as one. A malformed value degrades to no
    logo rather than failing the whole report."""
    if not logo_data_uri or not logo_data_uri.startswith("data:"):
        return None
    try:
        _, encoded = logo_data_uri.split(",", 1)
        return base64.b64decode(encoded)
    except (ValueError, base64.binascii.Error):
        return None


def _logo_flowable(logo_bytes: bytes) -> Image:
    # ImageReader is used only to measure the source image's aspect ratio —
    # ReportLab's Image flowable itself wants a fresh, unconsumed file-like
    # object, not an ImageReader instance (see sales_pdf.py's own comment
    # for the exact TypeError this avoids).
    width_px, height_px = ImageReader(BytesIO(logo_bytes)).getSize()
    aspect = width_px / height_px if height_px else 1.0
    height = _MAX_LOGO_HEIGHT
    width = min(height * aspect, _MAX_LOGO_WIDTH)
    if width == _MAX_LOGO_WIDTH:
        height = width / aspect
    image = Image(BytesIO(logo_bytes), width=width, height=height)
    image.hAlign = "CENTER"
    return image


def _particulars(invoice: Invoice) -> str:
    """What a human would call this line — the vendor if OCR found one,
    falling back to whatever else identifies the document, same fallback
    order the Saved Records list itself uses."""
    return invoice.vendor_name or invoice.invoice_number or invoice.filename or "—"


def _payment_label(invoice: Invoice) -> str:
    return invoice.payment_method.value.title() if invoice.payment_method else "Not Specified"


def _document_type_label(invoice: Invoice) -> str:
    """Same title-casing as the frontend's documentTypeLabel() (app.records.
    tsx) — "minute_sheet" -> "Minute Sheet" — so the on-screen list and the
    downloaded report never describe a document's type two different ways."""
    detected = invoice.detected_document_type
    return detected.replace("_", " ").title() if detected else "Unknown"


def _fit(width_px: float, height_px: float) -> tuple[float, float]:
    aspect = (width_px / height_px) if height_px else 1.0
    width, height = _MAX_APPENDIX_WIDTH, _MAX_APPENDIX_WIDTH / aspect
    if height > _MAX_APPENDIX_HEIGHT:
        height = _MAX_APPENDIX_HEIGHT
        width = height * aspect
    return width, height


def render_category_report_pdf(
    *,
    category_label: str,
    invoices: list[Invoice],
    entry_images: dict[UUID, bytes],
    category_total: float,
    company_name: str | None = None,
    logo_data_uri: str | None = None,
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=_PAGE_SIZE,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"{category_label} — Expense Report", author=company_name or "FinPilot AI",
    )

    styles = getSampleStyleSheet()
    # alignment: 0 = left, 1 = center — the whole header block (logo, company
    # name, title, meta line) is centered, not just the logo on its own, for
    # the same reason a letterhead centers its whole mark-plus-name block
    # rather than just the mark.
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=18, textColor=_HEADING, alignment=1,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    company_style = ParagraphStyle("company", parent=styles["Normal"], fontSize=11, textColor=_TEXT, alignment=1)
    meta_style = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8, textColor=_MUTED, alignment=1)
    value_style = ParagraphStyle("value", parent=styles["Normal"], fontSize=9, textColor=_TEXT)
    # Appendix details card styles — a receipt image on its own answers
    # "what does this look like", not "what did we record about it"; a
    # reader matching the report back to a physical folder of receipts
    # needs both without flipping back to the summary table each time.
    entry_index_style = ParagraphStyle("entry_index", parent=styles["Normal"], fontSize=8, textColor=_MUTED)
    entry_heading_style = ParagraphStyle(
        "entry_heading", parent=styles["Normal"], fontSize=14, textColor=_HEADING, fontName="Helvetica-Bold",
    )
    detail_label_style = ParagraphStyle(
        "detail_label", parent=styles["Normal"], fontSize=7.5, textColor=_MUTED, fontName="Helvetica-Bold",
    )
    detail_value_style = ParagraphStyle("detail_value", parent=styles["Normal"], fontSize=10, textColor=_TEXT)
    amount_style = ParagraphStyle(
        "amount", parent=styles["Normal"], fontSize=13, textColor=_HEADING, fontName="Helvetica-Bold", alignment=2,
    )

    story: list = []
    logo = _decode_logo(logo_data_uri)
    if logo is not None:
        try:
            story.append(_logo_flowable(logo))
            story.append(Spacer(1, 3 * mm))
        except Exception:
            logger.warning("Could not decode company logo for category report — continuing without it")

    story.append(Paragraph(company_name or "FinPilot AI", company_style))
    story.append(Paragraph(f"{category_label} — Expense Report", title_style))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')} · {len(invoices)} "
        f"{'entry' if len(invoices) == 1 else 'entries'}", meta_style,
    ))
    story.append(Spacer(1, 6 * mm))

    rows: list[list] = [["Date", "Particulars", "Payment", "Amount"]]
    for invoice in invoices:
        invoice_date = invoice.invoice_date.isoformat() if isinstance(invoice.invoice_date, date_type) else "—"
        payment = invoice.payment_method.value.title() if invoice.payment_method else "—"
        rows.append([invoice_date, Paragraph(_particulars(invoice), value_style), payment, _money(invoice.total)])

    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _HEADER_TEXT),
        ("TEXTCOLOR", (0, 1), (-1, -1), _TEXT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ZEBRA]),
    ]
    if len(rows) == 1:
        rows.append(["—", Paragraph("No entries in this category yet.", value_style), "—", ""])
    items_table = Table(rows, colWidths=[24 * mm, 92 * mm, 26 * mm, 32 * mm], repeatRows=1)
    items_table.setStyle(TableStyle(table_style))
    story.extend([items_table, Spacer(1, 4 * mm)])

    totals = Table([["Total", _money(category_total)]], colWidths=[142 * mm, 32 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, -1), _HEADING),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, _ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(totals)

    # Appendix — one labeled page per entry with an actual source image, in
    # the same order as the table above so a reader can match one to the
    # other without hunting.
    has_appendix = any(invoice.id in entry_images for invoice in invoices)
    if has_appendix:
        story.append(PageBreak())
        story.append(Paragraph("Appendix — Source Documents", title_style))
        story.append(Spacer(1, 4 * mm))
        first = True
        appendix_total = sum(1 for invoice in invoices if invoice.id in entry_images)
        entry_number = 0
        for invoice in invoices:
            image_bytes = entry_images.get(invoice.id)
            if image_bytes is None:
                continue
            # The bytes reached here because the S3 fetch succeeded, but
            # that says nothing about whether they actually decode as an
            # image — a truncated upload or an unexpected format would
            # otherwise crash the whole report over one bad entry. Caught
            # here, at the point of decode, rather than trusting the
            # caller: this is the only place that actually knows.
            try:
                width_px, height_px = ImageReader(BytesIO(image_bytes)).getSize()
            except Exception:
                logger.warning("Could not decode source image for invoice %s in category report", invoice.id)
                continue

            if not first:
                story.append(PageBreak())
            first = False
            entry_number += 1

            invoice_date = invoice.invoice_date.isoformat() if isinstance(invoice.invoice_date, date_type) else "Unknown date"

            # A details card — the same fields Saved Records shows on
            # screen (Date, Vendor/Payee, Document Type, Payment, Amount) —
            # sitting right above the receipt it describes, so someone
            # working from a printed/downloaded copy never has to flip back
            # to the summary table on page 1 to know what they're looking
            # at. Category is deliberately not repeated here: every entry
            # in this report already shares the one category named in the
            # page title above.
            story.append(Paragraph(f"Entry {entry_number} of {appendix_total}", entry_index_style))
            story.append(Paragraph(_particulars(invoice), entry_heading_style))
            story.append(Spacer(1, 2 * mm))

            details = Table(
                [
                    [
                        Paragraph("DATE", detail_label_style), Paragraph("PAYMENT", detail_label_style),
                        Paragraph("DOCUMENT TYPE", detail_label_style), "",
                    ],
                    [
                        Paragraph(invoice_date, detail_value_style), Paragraph(_payment_label(invoice), detail_value_style),
                        Paragraph(_document_type_label(invoice), detail_value_style),
                        Paragraph(f"Rs. {_money(invoice.total)}" if invoice.total is not None else "—", amount_style),
                    ],
                ],
                colWidths=[45 * mm, 40 * mm, 45 * mm, 40 * mm],
            )
            details.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("SPAN", (3, 0), (3, 1)),
                ("VALIGN", (3, 0), (3, 1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                ("LINEBELOW", (0, 1), (-1, 1), 0.6, _RULE),
                ("BACKGROUND", (0, 0), (-1, -1), _ZEBRA),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
            ]))
            story.append(details)
            story.append(Spacer(1, 5 * mm))

            width, height = _fit(width_px, height_px)
            story.append(Image(BytesIO(image_bytes), width=width, height=height))

    doc.build(story)
    return buffer.getvalue()
