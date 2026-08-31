# HR Service — employee directory and payroll processing

**Status:** Implemented (written alongside the build — see §9 for what was
actually verified).

## 1. Summary

Architecture report §5.5's HR Service, built close to as originally
specified: an employee directory plus a payroll processing flow. The one
real addition beyond §5.5's own text is a hand-off into Transactions
Service on payroll processing, following the exact pattern
`invoice-service`'s send-to-accounting hook already established for
purchase invoices.

## 2. Goals

- Real employee CRUD, replacing `/app/employees`'s hardcoded 8-employee
  dummy array.
- A payroll model that distinguishes an employee's *current* recurring
  figures (editable any time) from what was *actually paid* for a given
  month (an immutable snapshot) — a real distinction the flat dummy data
  had no way to express.
- `POST /employees/payroll/process` that is safely idempotent — running it
  twice in the same month must not pay anyone twice, and must still
  correctly handle someone hired between two runs in the same month.
- Close the loop the Transactions Service phase left open: that phase
  derived the Dashboard's "Employee Salaries" KPI from the Expense
  ledger's "Salaries" category because no payroll system existed yet.
  Processing a real payroll run now books into that same category, so the
  KPI becomes backed by real payroll data with **no changes to
  Transactions Service or the Dashboard**.

## 3. Non-goals / explicitly not touched

- **No actual money movement.** "Process Payroll" records that employees
  were paid and books an accounting entry for it — it does not integrate
  with a bank, disburse funds, or generate payslip documents. Architecture
  report §5.5 doesn't ask for that either.
- **No org chart / manager hierarchy, no leave/attendance tracking.** Not
  in §5.5's scope, not added here.
- **Payroll history exists in the API but is not surfaced in the UI yet.**
  `GET /employees/payroll/history` is built and tested; `/app/employees`
  doesn't render it. The page's dummy version had no history concept
  either, so this isn't a regression — it's a real capability with no
  frontend consumer yet, the same "built but not wired to a page" state a
  few other endpoints in this codebase are already in (see invoice-
  service's own `getScanStatus`/`getSalesScanStatus`, noted in the Revenue
  Manager spec).

## 4. v1 limitations (deliberate)

- **The payroll→expense hand-off is best-effort, not transactional** — the
  exact same tradeoff as Invoice Service's send-to-accounting hook. If
  Transactions Service is unreachable when payroll is processed, employees
  are still recorded as paid in this service's own `PayrollRecord` history
  (nothing there is lost or rolled back), but no Expense is booked and
  nothing retries it later. `PayrollProcessResult.expense_booked` tells the
  caller which happened; the frontend surfaces a warning toast when it's
  `false`.
- **A payroll run's "amount booked" is derived per-call, not looked up
  after the fact.** Two processing calls in the same month (e.g. a new
  hire added mid-cycle) each book their own, correctly-sized Expense using
  a batch-unique `reference_id` — see §6.1 for why a single
  `"PAYROLL-{period}"` key would have under-booked the second call.
- **No department enum, same reasoning as every other "suggested list" in
  this codebase** (vendor category, expense category): `SUGGESTED_
  DEPARTMENTS` is offered to the UI, never enforced.
- **Not live/Docker-verified** — see §9.

## 5. Data model

```
Employee: id, company_id, name, department (str, suggested list),
          salary_pkr, bonus_pkr, deductions_pkr — the *current* recurring
          figures, editable any time
          joining_date, active, created_at, updated_at

PayrollRecord: id, company_id, employee_id (real FK — both tables live in
               this service's own database, unlike Expense's vendor_id/
               invoice_id which name records in a different service),
               period ("YYYY-MM"),
               salary_pkr, bonus_pkr, deductions_pkr, net_salary_pkr —
               snapshotted at processing time, independent of whatever
               Employee says today
               processed_at, created_at
               unique (employee_id, period)
```

`net_salary_pkr` and `payment_status` on the API response are **computed
on every read**, never stored on Employee: the former is a pure function
of the other three fields, and the latter reflects whether a
`PayrollRecord` exists for the current period — both would drift the
moment either fact changed if they were cached columns instead.

## 6. Backend — `hr-service` (port 8004)

New service, `backend/services/hr-service/`, scaffolded identically to
`transactions-service`/`vendors-service`.

**Employee routes** (`app/api/routes/employees.py`, prefix `/employees`):
- `GET /employees/options` — suggested departments
- `GET /employees` — list (filter by department, active, name search),
  each row carrying computed `net_salary_pkr` and this-month
  `payment_status`
- `POST /employees` — create
- `GET /employees/{id}`, `PUT /employees/{id}`

**Payroll routes** (`app/api/routes/payroll.py`, prefix
`/employees/payroll`, registered **before** `employees.py` in `main.py` —
the same route-ordering rule `sales_scanner_router` documents against
`sales_router`, so `/employees/payroll` is never parsed as
`/employees/{id}` with `id="payroll"`):
- `GET /employees/payroll` — this month's payroll picture computed from
  active employees' *current* figures (see the schema's own docstring for
  why this is "what it would cost today," not a completed run's numbers)
- `POST /employees/payroll/process` — pays every active employee not
  already covered this period; §6.1 covers the booking hand-off
- `GET /employees/payroll/history` — past periods, aggregated in Python
  (SQLite-portable, same reasoning as Invoice Service's own
  `unlinked_vendor_groups` and Transactions Service's `_expenses_by_month`)

### 6.1 The payroll→expense hand-off

`process_payroll` computes exactly which employees are new to this
period's run, creates their `PayrollRecord`s, and — only when that set is
non-empty — calls Transactions Service's new `POST /expenses/book-from-
payroll` with the sum of just those employees' net pay.

The `reference_id` sent is `PAYROLL-{period}-{8 random hex chars}`,
generated fresh per call, **not** a fixed `PAYROLL-{period}` key. A fixed
key would make Transactions Service's idempotency check treat a
legitimate *second* batch in the same month (a new hire processed after
the first run already happened) as a duplicate of the first, silently
dropping that employee's pay from the books. A random per-call key avoids
that while still giving Transactions Service something to key an
exact-retry idempotency check on.

### 6.2 `transactions-service` additions

- **`ExpenseSource.payroll`** — new enum value (migration
  `20260829_02_add_payroll_expense_source.py`, an additive `ALTER TYPE ...
  ADD VALUE`, safe on Postgres 12+).
- **`POST /expenses/book-from-payroll`** — books an approved, `category=
  "Salaries"` Expense. Idempotent on `(company_id, source=payroll,
  reference_id)` as a courtesy against an exact request retry, not a hard
  DB constraint the way `book-from-invoice`'s `invoice_id` uniqueness is —
  see §6.1 for why the real correctness here lives on HR Service's side.

## 7. Frontend

- **`hr-service.ts`** (new) — mirrors `transactions-service.ts`'s client
  shape.
- **`app.employees.tsx`** (rewritten) — real summary cards (Gross Payroll/
  Deductions/Net Payable/Bonuses, from `GET /employees/payroll`), an "Add
  Employee" dialog, a "Process Payroll" button (with a warning toast if
  the expense booking side failed), a searchable salary sheet, and a
  Payroll-by-Department chart computed client-side from the listed
  employees — same computation the old dummy page did, now over real data.

## 8. Testing

- **hr-service**: 25 new tests (`test_employee_routes.py`,
  `test_payroll_routes.py`) — CRUD, tenancy scoping, computed
  `net_salary_pkr`/`payment_status`, per-employee processing idempotency,
  the mid-month-new-hire batching behaviour, history aggregation, and the
  booking hook's degrade-not-fail behaviour (Transactions Service patched
  to fail).
- **transactions-service**: +4 tests for `book-from-payroll` (books
  correctly, repeat-call idempotency, two distinct batches both booking,
  and showing up in the category breakdown) — total suite now 37.
- **gateway**: routing tests updated — `/api/v1/employees` moved from the
  "not built yet" list to the "built, requires auth" one.
- Frontend `tsc --noEmit` clean.

## 9. Verified — locally only (no Docker build, no deploy)

- **hr-service**: 25/25 tests passing (new service).
- **transactions-service**: 37/37 tests passing (was 33 before this
  phase's 4 `book-from-payroll` additions).
- **gateway**: 43/43 tests passing (was 42).
- Frontend `tsc --noEmit` clean across the whole project.
- **Not run**: no Docker build/deploy, no live payroll run against a real
  stack — explicit constraint for this phase, same as Transactions
  Service's own §9/§11.3.
