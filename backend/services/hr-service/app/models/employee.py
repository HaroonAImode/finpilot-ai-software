import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Architecture report §5.5's own list. Suggestions, not an enforced enum —
#: same reasoning as vendors-service's SUGGESTED_CATEGORIES: a real SME's
#: departments do not fit eight buckets, and a new one would otherwise need
#: an ALTER TYPE migration before someone could type it.
SUGGESTED_DEPARTMENTS = (
    "Development", "Design", "Product", "QA", "Finance", "Accounts", "HR", "Administration",
    "Operations", "Sales", "Marketing", "Procurement", "IT", "Customer Support", "Legal", "Logistics",
)

#: Same "suggestion, not an enforced enum" reasoning as SUGGESTED_DEPARTMENTS
#: above — a real SME's job titles never fit a fixed list either, so this is
#: just what the Role picker offers first; typing something else is not
#: blocked at the schema level, only the frontend's Select currently only
#: offers these (see EmployeeOptions.roles).
SUGGESTED_ROLES = (
    "CEO", "COO", "CFO", "CTO",
    "HR Manager", "HR Executive", "HR",
    "Program Manager", "Project Manager", "Product Manager", "Business Analyst", "Team Lead",
    "Software Engineer", "Frontend Developer", "Backend Developer", "Full Stack Developer",
    "Mobile App Developer", "DevOps Engineer", "AI Engineer", "Data Scientist", "Data Analyst",
    "QA Engineer", "QA",
    "Designer", "UI/UX Designer",
    "Accountant", "Finance Manager",
    "Sales Executive", "Sales Manager", "Marketing Executive", "Marketing Manager",
    "Procurement Officer", "Office Administrator", "Customer Support Executive",
    "IT Support Engineer", "Network Administrator", "Legal Advisor", "Intern",
)


class Employee(Base):
    """One person on this company's payroll.

    `salary_pkr`/`bonus_pkr`/`deductions_pkr` here are the *current*
    recurring figures — what this employee is paid going forward, editable
    at any time. What was actually paid for a given month is a separate,
    immutable snapshot: see PayrollRecord below. A raise this month must
    not silently rewrite what last month's payslip said someone earned.
    """

    __tablename__ = "employee"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Job title, e.g. "Backend Developer" or "HR Manager" — distinct from
    #: department (a person's team) the same way it is in any real company.
    #: Nullable only so the additive migration doesn't need to guess a value
    #: for employees created before this column existed; every employee
    #: created going forward always has one (see EmployeeCreate.role).
    role: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    salary_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    bonus_pkr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deductions_pkr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    joining_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: An employee who has left is kept, not deleted — their PayrollRecord
    #: history is real payroll data. Inactive employees are simply excluded
    #: from the next processing run.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
