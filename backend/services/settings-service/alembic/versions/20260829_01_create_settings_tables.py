"""create company, automation_settings and tax_settings tables

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

FILER_STATUS_VALUES = ("filer", "non_filer")

#: Created explicitly rather than relying on create_table's implicit side
#: effect — see vendors-service's own first migration for the reasoning.
filer_status = postgresql.ENUM(*FILER_STATUS_VALUES, name="filer_status")


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("ntn", sa.String(32), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("logo_url", sa.String(1000), nullable=True),
        sa.Column("industry", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_company_company_id", "company", ["company_id"])
    op.create_unique_constraint("uq_company_company_id", "company", ["company_id"])

    op.create_table(
        "automation_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auto_categorize_expenses", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_detect_duplicates", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_insights", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("smart_vendor_suggestions", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_settings_company_id", "automation_settings", ["company_id"])
    op.create_unique_constraint("uq_automation_settings_company_id", "automation_settings", ["company_id"])

    filer_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "tax_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("default_gst_rate", sa.Float(), nullable=False, server_default="18.0"),
        sa.Column("withholding_tax_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "filer_status",
            postgresql.ENUM(*FILER_STATUS_VALUES, name="filer_status", create_type=False),
            nullable=False, server_default="filer",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tax_settings_company_id", "tax_settings", ["company_id"])
    op.create_unique_constraint("uq_tax_settings_company_id", "tax_settings", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_tax_settings_company_id", table_name="tax_settings")
    op.drop_table("tax_settings")
    filer_status.drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_automation_settings_company_id", table_name="automation_settings")
    op.drop_table("automation_settings")
    op.drop_index("ix_company_company_id", table_name="company")
    op.drop_table("company")
