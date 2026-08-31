"""create invoice, invoice_item, ai_job tables

Revision ID: 20260821_01
Revises:
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260821_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Enum("purchase", "sale", name="invoice_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processed", "needs_review", "needs_review_high_priority", "validated", "sent_to_accounting",
                name="invoice_status",
            ),
            nullable=False,
        ),
        sa.Column("vendor_name", sa.String(length=500), nullable=True),
        sa.Column("invoice_number", sa.String(length=128), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("ntn", sa.String(length=32), nullable=True),
        sa.Column("subtotal", sa.Float(), nullable=True),
        sa.Column("tax_rate", sa.Float(), nullable=True),
        sa.Column("tax_amount", sa.Float(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("document_confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=16), nullable=False),
        sa.Column("review_flags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("raw_extraction_json", postgresql.JSON(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mimetype", sa.String(length=255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(length=1000), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_company_id", "invoice", ["company_id"])
    # Rule 7.1: the same file (by content) uploaded twice for the same
    # company is a duplicate, not a second invoice — enforced at the DB
    # level, not just checked in application code, so a race between two
    # concurrent uploads of the same file can't slip past an app-level check.
    op.create_unique_constraint(
        "uq_invoice_company_file_hash", "invoice", ["company_id", "file_hash"]
    )

    op.create_table(
        "invoice_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=True),
        sa.Column("rate", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("arithmetic_check", sa.String(length=16), nullable=False),
        sa.Column("review_flags", postgresql.ARRAY(sa.String()), nullable=False),
    )
    op.create_index("ix_invoice_item_invoice_id", "invoice_item", ["invoice_id"])

    op.create_table(
        "ai_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "invoice_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status", sa.Enum("queued", "processing", "done", "failed", name="ai_job_status"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_job_company_id", "ai_job", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_job_company_id", table_name="ai_job")
    op.drop_table("ai_job")
    op.drop_index("ix_invoice_item_invoice_id", table_name="invoice_item")
    op.drop_table("invoice_item")
    op.drop_constraint("uq_invoice_company_file_hash", "invoice", type_="unique")
    op.drop_index("ix_invoice_company_id", table_name="invoice")
    op.drop_table("invoice")
    op.execute("DROP TYPE IF EXISTS ai_job_status")
    op.execute("DROP TYPE IF EXISTS invoice_status")
    op.execute("DROP TYPE IF EXISTS invoice_type")
