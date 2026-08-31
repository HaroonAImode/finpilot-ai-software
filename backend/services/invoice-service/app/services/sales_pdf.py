"""Renders a sales invoice to PDF.

ReportLab rather than a HTML-to-PDF engine (WeasyPrint, wkhtmltopdf): those
pull in system libraries — Cairo/Pango or a bundled Chromium — which would
make this service's image substantially heavier and its build
platform-sensitive. ReportLab is pure Python, so `pip install` is the whole
story, and the output here is a simple fixed layout that gains nothing from
a CSS engine.

Deliberately no dependency on the OCR stack: this generates a document, it
does not read one.

Four templates (architecture-adjacent addition, not in the original §5.3
text): `classic`/`modern`/`midnight`/`minimal`, plus an optional logo in
one of three placements. Both come from Settings Service's Company
profile (see settings_service_client.py) — best-effort, so an
unreachable Settings Service still produces a correct, plain-classic
invoice rather than failing the download.
"""
import base64
from datetime import date as date_type
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Invoice

_PAGE_SIZE = A4
_MAX_LOGO_HEIGHT = 16 * mm
_MAX_LOGO_WIDTH = 50 * mm


class _Palette:
    def __init__(self, *, page_bg, accent, heading, text, muted, rule, header_text, zebra):
        self.page_bg = page_bg
        self.accent = accent
        self.heading = heading
        self.text = text
        self.muted = muted
        self.rule = rule
        self.header_text = header_text
        self.zebra = zebra


_PALETTES = {
    "classic": _Palette(
        page_bg=None, accent=colors.HexColor("#0F766E"), heading=colors.HexColor("#0F766E"),
        text=colors.HexColor("#111827"), muted=colors.HexColor("#6B7280"), rule=colors.HexColor("#E5E7EB"),
        header_text=colors.white, zebra=colors.HexColor("#F9FAFB"),
    ),
    "modern": _Palette(
        page_bg=None, accent=colors.HexColor("#4F46E5"), heading=colors.HexColor("#4F46E5"),
        text=colors.HexColor("#111827"), muted=colors.HexColor("#6B7280"), rule=colors.HexColor("#E0E7FF"),
        header_text=colors.white, zebra=colors.HexColor("#EEF2FF"),
    ),
    "midnight": _Palette(
        page_bg=colors.HexColor("#111827"), accent=colors.HexColor("#F59E0B"), heading=colors.HexColor("#F59E0B"),
        text=colors.HexColor("#F3F4F6"), muted=colors.HexColor("#9CA3AF"), rule=colors.HexColor("#374151"),
        header_text=colors.HexColor("#111827"), zebra=colors.HexColor("#1F2937"),
    ),
    "minimal": _Palette(
        page_bg=None, accent=colors.HexColor("#111827"), heading=colors.HexColor("#111827"),
        text=colors.HexColor("#111827"), muted=colors.HexColor("#6B7280"), rule=colors.HexColor("#111827"),
        header_text=colors.HexColor("#111827"), zebra=None,
    ),
}


def _money(value: float | None) -> str:
    """Blank rather than 0.00 for a missing value — printing a zero would
    assert something the invoice does not actually say."""
    if value is None:
        return ""
    return f"{value:,.2f}"


def _decode_logo(logo_data_uri: str | None) -> bytes | None:
    """`logo_data_uri` is a `data:image/...;base64,...` string (Settings
    Service stores it exactly as the browser produced it — see that
    service's own Company.logo_url docstring). Returns None for anything
    that doesn't parse as one, rather than raising: a malformed value
    should not break someone's invoice download."""
    if not logo_data_uri or not logo_data_uri.startswith("data:"):
        return None
    try:
        _, encoded = logo_data_uri.split(",", 1)
        return base64.b64decode(encoded)
    except (ValueError, base64.binascii.Error):
        return None


def _logo_flowable(logo_bytes: bytes) -> Image:
    # ImageReader is used only to measure the source image's aspect ratio —
    # ReportLab's Image flowable itself wants a fresh, unconsumed
    # file-like object (or a path), not an ImageReader instance.
    width_px, height_px = ImageReader(BytesIO(logo_bytes)).getSize()
    aspect = width_px / height_px if height_px else 1.0
    height = _MAX_LOGO_HEIGHT
    width = min(height * aspect, _MAX_LOGO_WIDTH)
    if width == _MAX_LOGO_WIDTH:
        height = width / aspect
    return Image(BytesIO(logo_bytes), width=width, height=height)


def _page_background(palette: _Palette):
    """A `SimpleDocTemplate` draws flowables onto an otherwise blank
    (white) page — a dark template needs the page itself painted first,
    which platypus only exposes via this onPage callback, not a flowable."""
    if palette.page_bg is None:
        return None

    def _paint(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(palette.page_bg)
        canvas.rect(0, 0, doc.pagesize[0], doc.pagesize[1], stroke=0, fill=1)
        canvas.restoreState()

    return _paint


def render_sales_invoice_pdf(
    invoice: Invoice, *,
    company_name: str | None = None,
    template: str = "classic",
    logo_data_uri: str | None = None,
    logo_placement: str = "left",
) -> bytes:
    palette = _PALETTES.get(template, _PALETTES["classic"])
    buffer = BytesIO()
    on_page = _page_background(palette)
    doc = SimpleDocTemplate(
        buffer, pagesize=_PAGE_SIZE,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {invoice.invoice_number or ''}".strip(),
        author=company_name or "FinPilot AI",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=20, textColor=palette.heading, alignment=0, spaceAfter=2,
        fontName="Helvetica-Bold" if template != "minimal" else "Helvetica",
    )
    company_style = ParagraphStyle("company", parent=styles["Normal"], fontSize=10, textColor=palette.text)
    label_style = ParagraphStyle("label", parent=styles["Normal"], fontSize=8, textColor=palette.muted)
    value_style = ParagraphStyle("value", parent=styles["Normal"], fontSize=10, textColor=palette.text)

    story: list = []

    logo = _decode_logo(logo_data_uri)
    title_block = [
        Paragraph("SALES INVOICE", title_style),
        Paragraph(company_name or "FinPilot AI", company_style),
    ]
    if invoice.invoice_number:
        title_block.append(Paragraph(invoice.invoice_number, label_style))

    if logo is not None:
        logo_flowable = _logo_flowable(logo)
        if logo_placement == "center":
            logo_flowable.hAlign = "CENTER"
            story.append(logo_flowable)
            story.append(Spacer(1, 4 * mm))
            for p in title_block:
                p.style.alignment = 1  # center
            story.extend(title_block)
        elif logo_placement == "right":
            for p in title_block:
                p.style.alignment = 0
            header = Table([[title_block, logo_flowable]], colWidths=[130 * mm, 34 * mm])
            header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
            story.append(header)
        else:  # left (default)
            header = Table([[logo_flowable, title_block]], colWidths=[34 * mm, 130 * mm])
            header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(header)
    else:
        story.extend(title_block)

    story.append(Spacer(1, 8 * mm))

    # Header block: two columns of label/value pairs. Rows for fields that
    # were never set are dropped entirely rather than printed blank — an
    # empty "NTN" line implies the field exists and is unknown, which for a
    # user-authored invoice is simply untrue.
    header_rows: list[list] = []
    pairs = [
        ("BILLED TO", invoice.customer_name),
        ("INVOICE DATE", invoice.invoice_date.isoformat() if isinstance(invoice.invoice_date, date_type) else None),
        ("NTN", invoice.ntn),
    ]
    for name, val in pairs:
        if val:
            header_rows.append([Paragraph(name, label_style), Paragraph(str(val), value_style)])

    if header_rows:
        header_info = Table(header_rows, colWidths=[35 * mm, 130 * mm])
        header_info.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.extend([header_info, Spacer(1, 8 * mm)])

    # Line items
    item_rows: list[list] = [["#", "Description", "Qty", "Rate", "Amount"]]
    for index, item in enumerate(invoice.items, start=1):
        item_rows.append([
            str(index),
            Paragraph(item.description or "", value_style),
            _money(item.qty),
            _money(item.rate),
            _money(item.amount),
        ])

    items_style = [
        ("BACKGROUND", (0, 0), (-1, 0), palette.accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), palette.header_text),
        ("TEXTCOLOR", (0, 1), (-1, -1), palette.text),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, palette.rule),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if palette.zebra is not None:
        items_style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white if palette.page_bg is None else palette.page_bg, palette.zebra]))

    items_table = Table(item_rows, colWidths=[10 * mm, 84 * mm, 22 * mm, 26 * mm, 32 * mm], repeatRows=1)
    items_table.setStyle(TableStyle(items_style))
    story.extend([items_table, Spacer(1, 6 * mm)])

    # Totals, right-aligned under the amount column.
    totals_rows = [
        ["Subtotal", _money(invoice.subtotal)],
        [f"Tax ({(invoice.tax_rate or 0) * 100:.2f}%)", _money(invoice.tax_amount)],
        ["Total", _money(invoice.total)],
    ]
    totals = Table(totals_rows, colWidths=[42 * mm, 32 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), palette.text),
        ("LINEABOVE", (0, 2), (-1, 2), 0.8, palette.accent),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 2), (-1, 2), palette.heading),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals)

    if on_page is not None:
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    else:
        doc.build(story)
    return buffer.getvalue()
