"""create report table

Revision ID: 20260829_01
Revises:
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260829_01"
down_revision = None
branch_labels = None
depends_on = None

REPORT_TYPE_VALUES = ("profit_loss", "balance_sheet", "cash_flow", "tax_summary", "sales", "purchases")
REPORT_STATUS_VALUES = ("queued", "generating", "ready", "failed")

#: Created explicitly rather than relying on create_table's implicit side
#: effect — see vendors-service's own first migration for the reasoning.
report_type = postgresql.ENUM(*REPORT_TYPE_VALUES, name="report_type")
report_status = postgresql.ENUM(*REPORT_STATUS_VALUES, name="report_status")


def upgrade() -> None:
    report_type.create(op.get_bind(), checkfirst=True)
    report_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "report",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", postgresql.ENUM(*REPORT_TYPE_VALUES, name="report_type", create_type=False), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "status", postgresql.ENUM(*REPORT_STATUS_VALUES, name="report_status", create_type=False),
            nullable=False, server_default="ready",
        ),
        sa.Column("payload_json", postgresql.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_report_company_id", "report", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_report_company_id", table_name="report")
    op.drop_table("report")
    report_status.drop(op.get_bind(), checkfirst=True)
    report_type.drop(op.get_bind(), checkfirst=True)
