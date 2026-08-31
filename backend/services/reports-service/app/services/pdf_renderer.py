"""Renders a ReportPayload to PDF bytes — one generic layout for every
report type, via ReportLab's platypus layer (high-level flowables, not
raw canvas drawing).

ReportLab over WeasyPrint (architecture report §5.9 named either):
WeasyPrint needs native GTK/Pango libraries that add real weight to a
slim Docker image; ReportLab ships as a normal Python wheel with no
external system dependency, matching how every other service in this
codebase stays a plain `pip install`.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas.payload import ReportPayload

_STYLES = getSampleStyleSheet()
_NOTE_STYLE = ParagraphStyle("Note", parent=_STYLES["Normal"], fontSize=8, textColor=colors.grey)


def render_pdf(payload: ReportPayload) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    story = [Paragraph(payload.title, _STYLES["Title"])]
    if payload.company_name:
        story.append(Paragraph(payload.company_name, _STYLES["Heading3"]))
    story.append(Paragraph(payload.period_label, _STYLES["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    summary_rows = [[line.label, line.value] for line in payload.summary]
    summary_table = Table(summary_rows, colWidths=[9 * cm, 6 * cm])
    summary_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ])
    )
    story.append(summary_table)

    if payload.table is not None:
        story.append(Spacer(1, 0.8 * cm))
        data = [payload.table.headers, *payload.table.rows]
        detail_table = Table(data, repeatRows=1)
        detail_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        story.append(detail_table)

    if payload.notes:
        story.append(Spacer(1, 0.8 * cm))
        for note in payload.notes:
            story.append(Paragraph(f"• {note}", _NOTE_STYLE))
            story.append(Spacer(1, 0.15 * cm))

    doc.build(story)
    return buffer.getvalue()
