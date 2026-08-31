"""Employee directory — architecture report §5.5. Payroll processing lives
in payroll.py, registered before this router in main.py so its literal
`/employees/payroll*` paths are matched before this file's `/{employee_id}`
pattern gets a chance to treat one of them as an id — the same ordering
rule sales_scanner_router documents against sales_router.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import SUGGESTED_DEPARTMENTS, SUGGESTED_ROLES, Employee, PayrollRecord
from app.schemas.employee import (
    EmployeeCreate, EmployeeListResponse, EmployeeOptions, EmployeeResponse, EmployeeUpdate,
)

router = APIRouter(prefix="/employees", tags=["employees"])


async def _get_employee_scoped(db: AsyncSession, employee_id: UUID, company_id: UUID) -> Employee:
    employee = await db.scalar(
        select(Employee).where(Employee.id == employee_id, Employee.company_id == company_id)
    )
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


async def _paid_this_period(db: AsyncSession, company_id: UUID, employee_ids: list[UUID]) -> set[UUID]:
    """Which of these employees already have a PayrollRecord for the
    current calendar month — one query for the whole page, not one per row.
    """
    if not employee_ids:
        return set()
    period = date.today().strftime("%Y-%m")
    rows = (
        await db.execute(
            select(PayrollRecord.employee_id).where(
                PayrollRecord.company_id == company_id, PayrollRecord.period == period,
                PayrollRecord.employee_id.in_(employee_ids),
            )
        )
    ).scalars().all()
    return set(rows)


def _to_response(employee: Employee, paid_ids: set[UUID]) -> EmployeeResponse:
    # Built field-by-field rather than EmployeeResponse.model_validate(employee):
    # net_salary_pkr and payment_status are computed, not columns on
    # Employee, so from_attributes has nothing to read them from.
    return EmployeeResponse(
        id=employee.id, name=employee.name, department=employee.department, role=employee.role,
        salary_pkr=employee.salary_pkr, bonus_pkr=employee.bonus_pkr, deductions_pkr=employee.deductions_pkr,
        net_salary_pkr=employee.salary_pkr + employee.bonus_pkr - employee.deductions_pkr,
        joining_date=employee.joining_date, active=employee.active,
        payment_status="paid" if employee.id in paid_ids else "pending",
        created_at=employee.created_at, updated_at=employee.updated_at,
    )


@router.get("/options", response_model=EmployeeOptions)
async def employee_options() -> EmployeeOptions:
    """Suggested departments and roles for the UI. Registered before
    /{employee_id} so the literal path is not parsed as an id."""
    return EmployeeOptions(departments=list(SUGGESTED_DEPARTMENTS), roles=list(SUGGESTED_ROLES))


@router.get("/", response_model=EmployeeListResponse)
async def list_employees(
    department: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive match on name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> EmployeeListResponse:
    query = select(Employee).where(Employee.company_id == company_id)
    count_query = select(func.count()).select_from(Employee).where(Employee.company_id == company_id)

    if department:
        query = query.where(Employee.department == department)
        count_query = count_query.where(Employee.department == department)
    if active is not None:
        query = query.where(Employee.active == active)
        count_query = count_query.where(Employee.active == active)
    if search:
        pattern = f"%{search}%"
        query = query.where(Employee.name.ilike(pattern))
        count_query = count_query.where(Employee.name.ilike(pattern))

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.order_by(Employee.name).offset(skip).limit(limit))
    ).scalars().all()

    paid_ids = await _paid_this_period(db, company_id, [r.id for r in rows])
    return EmployeeListResponse(
        employees=[_to_response(r, paid_ids) for r in rows], total=total, skip=skip, limit=limit,
    )


@router.post("/", response_model=EmployeeResponse, status_code=201)
async def create_employee(
    payload: EmployeeCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = Employee(
        company_id=company_id, name=payload.name, department=payload.department, role=payload.role,
        salary_pkr=payload.salary_pkr, bonus_pkr=payload.bonus_pkr, deductions_pkr=payload.deductions_pkr,
        joining_date=payload.joining_date, active=payload.active,
    )
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    # Brand new, so this month cannot have a PayrollRecord for them yet.
    return _to_response(employee, set())


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await _get_employee_scoped(db, employee_id, company_id)
    paid_ids = await _paid_this_period(db, company_id, [employee.id])
    return _to_response(employee, paid_ids)


@router.put("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: UUID, payload: EmployeeUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await _get_employee_scoped(db, employee_id, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(employee, field_name, value)
    await db.commit()
    await db.refresh(employee)
    paid_ids = await _paid_this_period(db, company_id, [employee.id])
    return _to_response(employee, paid_ids)


@router.delete("/{employee_id}", status_code=204)
async def delete_employee(
    employee_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> None:
    """Hard-deletes an employee — but only one who has never actually been
    paid. The moment a PayrollRecord exists for them, `employee_id`'s
    ON DELETE CASCADE would silently wipe real payroll history along with
    the row, which contradicts this model's own stated design (see
    Employee.active's docstring). For anyone with payroll history, the
    correct "remove" is PUT .../active=false, not DELETE.
    """
    employee = await _get_employee_scoped(db, employee_id, company_id)
    has_payroll_history = await db.scalar(
        select(func.count()).select_from(PayrollRecord).where(
            PayrollRecord.company_id == company_id, PayrollRecord.employee_id == employee_id,
        )
    )
    if has_payroll_history:
        raise HTTPException(
            status_code=409,
            detail="This employee has payroll history and cannot be deleted. Mark them inactive instead.",
        )
    await db.delete(employee)
    await db.commit()
