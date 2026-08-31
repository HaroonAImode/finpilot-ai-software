"""add role column to employee

Revision ID: 20260831_01
Revises: 20260829_01
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "20260831_01"
down_revision = "20260829_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable — existing employees predate this column and there is no
    # sensible role to backfill for them. Every employee created from now
    # on always has one (enforced at the Pydantic layer, EmployeeCreate.role).
    op.add_column("employee", sa.Column("role", sa.String(150), nullable=True))


def downgrade() -> None:
    op.drop_column("employee", "role")
