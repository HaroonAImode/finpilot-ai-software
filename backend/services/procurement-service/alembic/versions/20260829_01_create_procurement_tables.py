"""create purchase_request, purchase_order and vendor_quote tables

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

REQUEST_STATUS_VALUES = ("pending_approval", "approved", "rejected")
ORDER_STATUS_VALUES = ("in_transit", "delivered", "cancelled")

#: Created explicitly rather than relying on create_table's implicit side
#: effect — see vendors-service's and documents-service's own first
#: migrations for the reasoning (an UndefinedObjectError on a later
#: standalone add_column otherwise).
purchase_request_status = postgresql.ENUM(*REQUEST_STATUS_VALUES, name="purchase_request_status")
purchase_order_status = postgresql.ENUM(*ORDER_STATUS_VALUES, name="purchase_order_status")


def upgrade() -> None:
    purchase_request_status.create(op.get_bind(), checkfirst=True)
    purchase_order_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "purchase_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_description", sa.String(500), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("requester_name", sa.String(300), nullable=False),
        sa.Column("amount_pkr", sa.Float(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*REQUEST_STATUS_VALUES, name="purchase_request_status", create_type=False),
            nullable=False, server_default="pending_approval",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_purchase_request_company_id", "purchase_request", ["company_id"])

    op.create_table(
        "purchase_order",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "purchase_request_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_request.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_name", sa.String(500), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("amount_pkr", sa.Float(), nullable=False),
        sa.Column("expected_delivery", sa.Date(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*ORDER_STATUS_VALUES, name="purchase_order_status", create_type=False),
            nullable=False, server_default="in_transit",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_purchase_order_company_id", "purchase_order", ["company_id"])

    op.create_table(
        "vendor_quote",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "purchase_request_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_request.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_name", sa.String(500), nullable=False),
        sa.Column("price_pkr", sa.Float(), nullable=False),
        sa.Column("delivery_estimate", sa.String(100), nullable=True),
        sa.Column("quality_rating", sa.String(50), nullable=True),
        sa.Column("payment_terms", sa.String(100), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vendor_quote_company_id", "vendor_quote", ["company_id"])
    op.create_index("ix_vendor_quote_purchase_request_id", "vendor_quote", ["purchase_request_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_quote_purchase_request_id", table_name="vendor_quote")
    op.drop_index("ix_vendor_quote_company_id", table_name="vendor_quote")
    op.drop_table("vendor_quote")
    op.drop_index("ix_purchase_order_company_id", table_name="purchase_order")
    op.drop_table("purchase_order")
    op.drop_index("ix_purchase_request_company_id", table_name="purchase_request")
    op.drop_table("purchase_request")
    purchase_order_status.drop(op.get_bind(), checkfirst=True)
    purchase_request_status.drop(op.get_bind(), checkfirst=True)
