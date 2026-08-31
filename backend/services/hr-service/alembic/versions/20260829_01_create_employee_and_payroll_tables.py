"""create employee and payroll_record tables

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


def upgrade() -> None:
    op.create_table(
        "employee",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("salary_pkr", sa.Float(), nullable=False),
        sa.Column("bonus_pkr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("deductions_pkr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_employee_company_id", "employee", ["company_id"])

    op.create_table(
        "payroll_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "employee_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employee.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("salary_pkr", sa.Float(), nullable=False),
        sa.Column("bonus_pkr", sa.Float(), nullable=False),
        sa.Column("deductions_pkr", sa.Float(), nullable=False),
        sa.Column("net_salary_pkr", sa.Float(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_payroll_record_company_id", "payroll_record", ["company_id"])
    op.create_index("ix_payroll_record_employee_id", "payroll_record", ["employee_id"])
    # Makes POST /employees/payroll/process idempotent per employee — see
    # the model's own docstring.
    op.create_unique_constraint(
        "uq_payroll_record_employee_period", "payroll_record", ["employee_id", "period"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_payroll_record_employee_period", "payroll_record", type_="unique")
    op.drop_index("ix_payroll_record_employee_id", table_name="payroll_record")
    op.drop_index("ix_payroll_record_company_id", table_name="payroll_record")
    op.drop_table("payroll_record")
    op.drop_index("ix_employee_company_id", table_name="employee")
    op.drop_table("employee")
