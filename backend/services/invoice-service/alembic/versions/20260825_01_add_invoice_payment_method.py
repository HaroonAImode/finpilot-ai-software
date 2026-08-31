"""add invoice.payment_method

Revision ID: 20260825_01
Revises: 20260821_01
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "20260825_01"
down_revision = "20260821_01"
branch_labels = None
depends_on = None


#: `sa.Enum(...)` only auto-creates its backing PostgreSQL type as a side
#: effect of `create_table` (a DDL event fires for the whole table's
#: enum columns together). A standalone `add_column` on an *existing*
#: table gets no such event — confirmed live: without the explicit
#: `.create()` below, this migration failed on a real run with
#: `UndefinedObjectError: type "invoice_payment_method" does not exist`,
#: since `ALTER TABLE ... ADD COLUMN` ran before anything had created the
#: type it references.
payment_method_enum = sa.Enum("bank", "cash", name="invoice_payment_method")


def upgrade() -> None:
    payment_method_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("invoice", sa.Column("payment_method", payment_method_enum, nullable=True))


def downgrade() -> None:
    op.drop_column("invoice", "payment_method")
    payment_method_enum.drop(op.get_bind(), checkfirst=True)
