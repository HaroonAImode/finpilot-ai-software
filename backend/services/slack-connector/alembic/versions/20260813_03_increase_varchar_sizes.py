"""increase varchar column sizes for file metadata

Revision ID: 20260813_03
Revises: 20260813_02
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_03"
down_revision = "20260813_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Increase mimetype column size (MIME types can be 80+ chars)
    op.alter_column('file', 'mimetype',
                    existing_type=sa.String(length=64),
                    type_=sa.String(length=255),
                    existing_nullable=True)
    
    # Increase file_type column size (for complex file types)
    op.alter_column('file', 'file_type',
                    existing_type=sa.String(length=64),
                    type_=sa.String(length=255),
                    existing_nullable=False)
    
    # Increase external_type column size
    op.alter_column('file', 'external_type',
                    existing_type=sa.String(length=64),
                    type_=sa.String(length=255),
                    existing_nullable=True)
    
    # Increase category_source column size
    op.alter_column('file', 'category_source',
                    existing_type=sa.String(length=64),
                    type_=sa.String(length=255),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column('file', 'category_source',
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=64),
                    existing_nullable=False)
    
    op.alter_column('file', 'external_type',
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=64),
                    existing_nullable=True)
    
    op.alter_column('file', 'file_type',
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=64),
                    existing_nullable=False)
    
    op.alter_column('file', 'mimetype',
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=64),
                    existing_nullable=True)
