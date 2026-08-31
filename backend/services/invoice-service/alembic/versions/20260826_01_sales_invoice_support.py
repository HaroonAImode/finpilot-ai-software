"""sales invoice support: customer_name, and scan-only columns made nullable

Revision ID: 20260826_01
Revises: 20260825_01
Create Date: 2026-08-26

A *generated* sales invoice has no uploaded file and no extraction behind it,
so the columns that exist to record a scan are genuinely inapplicable to it:
there is no `s3_key` because nothing was stored, no `file_hash` because
nothing was hashed, and no `raw_extraction_json` because nothing was read.

Rather than fill those with sentinel values — which would make
"was this scanned?" unanswerable and quietly corrupt Rule 8.1's audit trail,
since a fake `{}` extraction is indistinguishable from a real empty one —
they become nullable. NULL then means exactly what it says: this invoice did
not come from a document.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260826_01"
down_revision = "20260825_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sales invoices are billed *to* a customer, not received *from* a vendor.
    # The architecture report's own model says `vendor_id or customer_id`; this
    # is the free-text half of that, matching the existing vendor_name column.
    op.add_column("invoice", sa.Column("customer_name", sa.String(500), nullable=True))

    for column in ("raw_extraction_json", "filename", "s3_key", "file_hash"):
        op.alter_column("invoice", column, nullable=True)
    op.alter_column("invoice", "size", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Deliberately not re-imposing NOT NULL: any sales invoice created while
    # this migration was applied has NULLs in these columns, so blindly
    # tightening the constraint would fail on real data. Backfill or delete
    # those rows first if a true rollback is ever needed.
    op.drop_column("invoice", "customer_name")
