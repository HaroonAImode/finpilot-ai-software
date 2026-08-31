"""create expense table

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

PAYMENT_METHOD_VALUES = ("bank_transfer", "online", "cheque", "cash", "card")
STATUS_VALUES = ("pending", "approved", "rejected")
SOURCE_VALUES = ("manual", "invoice")

#: Created explicitly rather than relying on create_table's implicit side
#: effect — see documents-service's own first migration for the real
#: failure that habit caused (UndefinedObjectError on a standalone
#: add_column), the same reasoning vendors-service's first migration notes.
expense_payment_method = postgresql.ENUM(*PAYMENT_METHOD_VALUES, name="expense_payment_method")
expense_status = postgresql.ENUM(*STATUS_VALUES, name="expense_status")
expense_source = postgresql.ENUM(*SOURCE_VALUES, name="expense_source")


def upgrade() -> None:
    expense_payment_method.create(op.get_bind(), checkfirst=True)
    expense_status.create(op.get_bind(), checkfirst=True)
    expense_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "expense",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("vendor_name", sa.String(500), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_pkr", sa.Float(), nullable=False),
        sa.Column(
            "payment_method",
            postgresql.ENUM(*PAYMENT_METHOD_VALUES, name="expense_payment_method", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*STATUS_VALUES, name="expense_status", create_type=False),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "source",
            postgresql.ENUM(*SOURCE_VALUES, name="expense_source", create_type=False),
            nullable=False, server_default="manual",
        ),
        sa.Column("reference_id", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_expense_company_id", "expense", ["company_id"])
    # An invoice can be booked as an expense at most once — see the model's
    # own docstring for why this is what makes book-from-invoice idempotent.
    op.create_unique_constraint("uq_expense_invoice_id", "expense", ["invoice_id"])


def downgrade() -> None:
    op.drop_constraint("uq_expense_invoice_id", "expense", type_="unique")
    op.drop_index("ix_expense_company_id", table_name="expense")
    op.drop_table("expense")
    expense_source.drop(op.get_bind(), checkfirst=True)
    expense_status.drop(op.get_bind(), checkfirst=True)
    expense_payment_method.drop(op.get_bind(), checkfirst=True)
