"""add company_id to installation for FinPilot multi-tenancy

Revision ID: 20260818_04
Revises: 20260813_03
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260818_04"
down_revision = "20260813_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installation",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Backfill any pre-existing row (there shouldn't be one in a fresh install)
    # before tightening to NOT NULL + UNIQUE, so the migration is safe to run
    # against a database that already has data.
    op.execute("UPDATE installation SET company_id = gen_random_uuid() WHERE company_id IS NULL")
    op.alter_column("installation", "company_id", nullable=False)
    op.create_unique_constraint("uq_installation_company_id", "installation", ["company_id"])


def downgrade() -> None:
    op.drop_constraint("uq_installation_company_id", "installation", type_="unique")
    op.drop_column("installation", "company_id")
