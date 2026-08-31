"""add invoice.vendor_id — the link to Vendors Service

Revision ID: 20260826_02
Revises: 20260826_01
Create Date: 2026-08-26

The architecture report's own model says `Invoice: ... vendor_id or
customer_id`, but until now there was only a free-text `vendor_name` written
from whatever OCR read. That is what forced
`scanner.py::_known_vendors()` to fuzzy-match against
`SELECT DISTINCT vendor_name FROM invoice` — matching against strings
scraped from past OCR output, so a misread ("A ARGENTO NEUE" for "ARGENTO
NEUE", seen live) became a permanent second vendor and then polluted every
later match.

`vendor_name` is deliberately **kept**, not replaced. It is the raw
extraction evidence, and Rule 8.1 says a human's or a matcher's decision
never overwrites what the engine actually read. `vendor_id` records the
decision; `vendor_name` records the evidence.

No foreign key: Vendors Service owns that table in its own database, so the
constraint cannot be enforced from here — the same convention the connectors
already use for cross-service references.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260826_02"
down_revision = "20260826_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True))
    # Indexed with company_id because every query that filters on it is
    # already company-scoped ("this vendor's invoices, for this company").
    op.create_index("ix_invoice_company_vendor", "invoice", ["company_id", "vendor_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_company_vendor", table_name="invoice")
    op.drop_column("invoice", "vendor_id")
