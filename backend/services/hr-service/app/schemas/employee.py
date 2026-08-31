from datetime import datetime
# Imported under an alias for the same reason transactions-service's
# expense.py does: a field below is literally named `joining_date`, not
# `date`, so this particular collision doesn't actually bite here — but the
# alias is kept anyway so a future field named `date` never silently hits
# the same Pydantic v2 gotcha (a field's own annotation resolving to
# NoneType when it shares its name with the imported type and has a
# default). See transactions-service's own schemas/expense.py for the
# confirmed repro.
from datetime import date as _date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmployeeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    department: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=150)
    salary_pkr: float = Field(gt=0)
    bonus_pkr: float = Field(default=0.0, ge=0)
    deductions_pkr: float = Field(default=0.0, ge=0)
    joining_date: _date
    active: bool = True

    @field_validator("name", "department", "role")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("cannot be blank")
        return cleaned


class EmployeeUpdate(BaseModel):
    """Every field optional; only those actually present are applied — the
    same model_fields_set convention used across this codebase's other PUT
    endpoints (e.g. vendors-service's VendorUpdate)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    department: Optional[str] = Field(default=None, min_length=1, max_length=100)
    role: Optional[str] = Field(default=None, min_length=1, max_length=150)
    salary_pkr: Optional[float] = Field(default=None, gt=0)
    bonus_pkr: Optional[float] = Field(default=None, ge=0)
    deductions_pkr: Optional[float] = Field(default=None, ge=0)
    joining_date: Optional[_date] = None
    active: Optional[bool] = None


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    department: str
    #: None only for employees created before this field existed.
    role: Optional[str] = None
    salary_pkr: float
    bonus_pkr: float
    deductions_pkr: float
    #: salary_pkr + bonus_pkr - deductions_pkr, as this employee's record
    #: reads *right now* — not a specific month's actual payslip. See
    #: PayrollRecord for what was actually paid.
    net_salary_pkr: float
    joining_date: _date
    active: bool
    #: "paid" if a PayrollRecord exists for this employee for the current
    #: calendar month, "pending" otherwise. Computed on every read, not
    #: stored — see routes/employees.py.
    payment_status: str
    created_at: datetime
    updated_at: datetime


class EmployeeListResponse(BaseModel):
    employees: list[EmployeeResponse]
    total: int
    skip: int
    limit: int


class EmployeeOptions(BaseModel):
    """Suggested departments and roles. Deliberately not enforced — see the
    model's SUGGESTED_DEPARTMENTS/SUGGESTED_ROLES for why."""

    departments: list[str]
    roles: list[str]
