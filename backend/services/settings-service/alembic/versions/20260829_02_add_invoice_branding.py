"""widen company.logo_url to text, add logo_placement and invoice_template

Revision ID: 20260829_02
Revises: 20260829_01
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260829_02"
down_revision = "20260829_01"
branch_labels = None
depends_on = None

LOGO_PLACEMENT_VALUES = ("left", "center", "right")
INVOICE_TEMPLATE_VALUES = ("classic", "modern", "midnight", "minimal")

logo_placement = postgresql.ENUM(*LOGO_PLACEMENT_VALUES, name="logo_placement")
invoice_template = postgresql.ENUM(*INVOICE_TEMPLATE_VALUES, name="invoice_template")


def upgrade() -> None:
    # logo_url now holds a data: URI (see Company's own docstring) — a
    # base64-encoded logo runs well past what String(1000) was ever sized
    # for.
    op.alter_column("company", "logo_url", type_=sa.Text(), existing_type=sa.String(1000))

    logo_placement.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "company",
        sa.Column(
            "logo_placement",
            postgresql.ENUM(*LOGO_PLACEMENT_VALUES, name="logo_placement", create_type=False),
            nullable=False, server_default="left",
        ),
    )

    invoice_template.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "company",
        sa.Column(
            "invoice_template",
            postgresql.ENUM(*INVOICE_TEMPLATE_VALUES, name="invoice_template", create_type=False),
            nullable=False, server_default="classic",
        ),
    )


def downgrade() -> None:
    op.drop_column("company", "invoice_template")
    invoice_template.drop(op.get_bind(), checkfirst=True)
    op.drop_column("company", "logo_placement")
    logo_placement.drop(op.get_bind(), checkfirst=True)
    op.alter_column("company", "logo_url", type_=sa.String(1000), existing_type=sa.Text())
