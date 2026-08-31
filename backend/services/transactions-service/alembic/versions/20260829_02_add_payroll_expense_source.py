"""add payroll to expense_source enum

Revision ID: 20260829_02
Revises: 20260829_01
Create Date: 2026-08-29
"""
from alembic import op

revision = "20260829_02"
down_revision = "20260829_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction
    # (this project runs postgres:16) — HR Service's payroll-processing
    # hand-off needs a third source value alongside manual/invoice.
    op.execute("ALTER TYPE expense_source ADD VALUE IF NOT EXISTS 'payroll'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value without rebuilding the type
    # and every column using it. Not attempted here — the same tradeoff
    # this codebase has not needed to solve for any other additive enum
    # value yet; a downgrade of this migration is a no-op by design.
    pass
