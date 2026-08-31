"""create document table

Revision ID: 20260826_01
Revises:
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260826_01"
down_revision = None
branch_labels = None
depends_on = None

CATEGORY_VALUES = ("invoices", "receipts", "reports", "contracts", "images", "other")
SCANNER_STATUS_VALUES = ("not_sent", "sent", "failed")

#: Created explicitly up front, and referenced on the columns with
#: `create_type=False`. `sa.Enum` only auto-creates its backing PostgreSQL
#: type as a side effect of `create_table`, and relying on that implicit
#: ordering is exactly what broke Invoice Service's `20260825_01` on a real
#: run (`UndefinedObjectError: type "invoice_payment_method" does not
#: exist`). Being explicit costs two lines and removes the whole class of
#: failure.
document_category = postgresql.ENUM(*CATEGORY_VALUES, name="document_category")
scanner_status = postgresql.ENUM(*SCANNER_STATUS_VALUES, name="document_scanner_status")


def upgrade() -> None:
    bind = op.get_bind()
    document_category.create(bind, checkfirst=True)
    scanner_status.create(bind, checkfirst=True)

    op.create_table(
        "document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mimetype", sa.String(255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(1000), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(*CATEGORY_VALUES, name="document_category", create_type=False),
            nullable=False, server_default="other",
        ),
        sa.Column(
            "scanner_status",
            postgresql.ENUM(*SCANNER_STATUS_VALUES, name="document_scanner_status", create_type=False),
            nullable=False, server_default="not_sent",
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_document_company_id", "document", ["company_id"])
    op.create_index("ix_document_file_hash", "document", ["file_hash"])
    # Every read path filters `company_id = ? AND deleted_at IS NULL`, so the
    # pair is indexed together rather than deleted_at on its own.
    op.create_index("ix_document_company_deleted", "document", ["company_id", "deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_document_company_deleted", table_name="document")
    op.drop_index("ix_document_file_hash", table_name="document")
    op.drop_index("ix_document_company_id", table_name="document")
    op.drop_table("document")
    bind = op.get_bind()
    scanner_status.drop(bind, checkfirst=True)
    document_category.drop(bind, checkfirst=True)
