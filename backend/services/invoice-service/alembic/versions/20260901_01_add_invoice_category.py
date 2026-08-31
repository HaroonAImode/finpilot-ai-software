"""add invoice.category — Saved Records' categorized cashbook view

Revision ID: 20260901_01
Revises: 20260826_02
Create Date: 2026-09-01

See docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md.
Free-text, nullable, indexed with company_id the same way vendor_id is
(20260826_02) — every query filtering on it is already company-scoped, and
`GET /invoices/categories/summary` groups by it across a whole company at
once. NULL means "not categorized yet" (shown/grouped as "Uncategorized"
everywhere), never backfilled with a guess.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_01"
down_revision = "20260826_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("category", sa.String(100), nullable=True))
    op.create_index("ix_invoice_company_category", "invoice", ["company_id", "category"])


def downgrade() -> None:
    op.drop_index("ix_invoice_company_category", table_name="invoice")
    op.drop_column("invoice", "category")
