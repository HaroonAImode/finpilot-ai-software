"""create vendor table

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

STATUS_VALUES = ("active", "inactive", "review")

#: Created explicitly rather than relying on `create_table`'s implicit
#: side effect — see documents-service's own first migration for the real
#: failure that habit caused (`UndefinedObjectError` on a standalone
#: add_column).
vendor_status = postgresql.ENUM(*STATUS_VALUES, name="vendor_status")


def upgrade() -> None:
    vendor_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "vendor",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("ntn", sa.String(32), nullable=True),
        sa.Column("payment_terms", sa.String(100), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(*STATUS_VALUES, name="vendor_status", create_type=False),
            nullable=False, server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_vendor_company_id", "vendor", ["company_id"])
    # Two vendors with the same name in one company are the duplicate this
    # service exists to prevent. Per company, not global — different
    # companies share supplier names constantly.
    op.create_unique_constraint("uq_vendor_company_name", "vendor", ["company_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_vendor_company_name", "vendor", type_="unique")
    op.drop_index("ix_vendor_company_id", table_name="vendor")
    op.drop_table("vendor")
    vendor_status.drop(op.get_bind(), checkfirst=True)
