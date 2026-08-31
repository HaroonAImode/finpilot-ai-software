# Transactions Service — expense ledger and Dashboard KPIs

**Status:** Implemented (this document was written alongside the build, not
before it — see §11 for what that means for how to read the rest of it).

## 1. Summary

Architecture report §5.4 names a "Transactions Service" that owns both
Revenue and Expenses plus the Dashboard's KPI/chart endpoints. By the time
this phase started, half of that scope no longer made sense to build as
originally planned: Revenue Manager (2026-08-27) had already turned
Invoice Service's own `invoice` table (`type=sale`) into the real revenue
ledger, with its own summary/monthly/top-customers aggregation. Building a
second Revenue table here would mean two sources of truth for the same
money, which is exactly the kind of duplication `docs/documents-and-vendors-
plan.md`'s vendor-spend design and this report's own Rule 1 already argue
against.

So Transactions Service, as actually built, owns **Expenses only** — the
half of §5.4 that had no existing home — plus the Dashboard KPI/chart
endpoints, which combine that Expense data with a read of Invoice Service's
existing sales summary for the revenue side. This is the same "derive,
don't duplicate" pattern vendors-service already uses for vendor spend.

## 2. Goals

- A real Expense ledger: manual entries with an approve/reject workflow,
  matching the `/app/expenses` page's existing (previously dummy) 4-card
  summary and category donut chart.
- A **real** hand-off on Invoice Service's `POST /invoices/{id}/send-to-
  accounting` for a purchase invoice — that endpoint's own docstring has
  said "once Transactions Service exists, this becomes a real call" since
  before this service was built. It now is one: sending a purchase invoice
  to accounting books a matching, approved Expense record here.
- Real Dashboard KPIs and both trend charts (`/app`), replacing the last
  hardcoded arrays on that page (`kpis`, `revenueVsExpenses`,
  `expenseCategories`, `cashFlow`, plus `revenueSources`/`upcomingPayments`,
  which had no real backing data and were replaced — see §3).

## 3. Non-goals / explicitly not touched

- **No Revenue table.** See §1. Revenue Manager's own scanned-sales
  summary is the ledger; this service reads it, never writes it.
- **No "Revenue Sources" channel breakdown.** The architecture report's
  dummy data invented a Retail/Wholesale/Online/Exports/Services
  breakdown with no extraction path behind it — nothing in the OCR
  pipeline detects a sales channel. The Dashboard's chart in that slot was
  replaced with **Top Customers** (real, from Invoice Service's sales
  summary) rather than either fabricating channels or leaving the panel
  empty.
- **No "Upcoming Payments" widget.** The dummy version implied an
  accounts-payable/due-date concept (`{title, due, amount}`) that doesn't
  exist anywhere in this schema — an Expense is money already spent, not a
  bill still owed. Replaced with **Pending Approvals** (real: this
  company's `status=pending` expenses), which is both genuine data and a
  more directly actionable panel.
- **No "Duplicate" invoice status bucket.** The architecture report's dummy
  Invoice Status pie had a Duplicate segment; there is no such
  `InvoiceStatus` value — a re-upload of an already-scanned file is
  rejected by the existing sha256 dedup check (Rule 7.1), not tagged with a
  status. The new `/invoices/status-summary` endpoint returns the real
  status names; the frontend groups them into Processed/Needs Review.
- **HR Service still doesn't exist.** "Employee Salaries" on the Dashboard
  is computed from this month's approved Expense entries in the
  "Salaries" category — a real number, not a fabricated payroll figure,
  but not sourced from a payroll system either.
- **No delta/trend arrows on KPI cards.** The dummy KPIs each carried a
  `delta` percentage vs. a prior period that nothing computes for real
  data (no historical snapshot is stored). Rather than fabricate a
  percentage, `KpiCard` was changed to render without one.

## 4. v1 limitations (deliberate)

- **Cash Balance is an approximation**, not a real bank balance: cumulative
  all-time revenue (from Invoice Service) minus cumulative all-time
  approved expenses (this service's own data). There is no cash/bank
  account model anywhere in this codebase.
- **The invoice→expense hand-off is best-effort, not transactional.** If
  Transactions Service is unreachable at the moment "Send to Accounting" is
  clicked, the purchase invoice's status still flips to
  `sent_to_accounting` (the accountant is not blocked), but no Expense
  record is created and nothing retries it later. This mirrors every other
  internal hand-off in this codebase (no RabbitMQ/outbox yet — architecture
  report §11b's long-standing note) and is logged as an error, not silently
  swallowed, but it is a real gap until a queue or outbox exists.
- **A scanned purchase invoice always books under "Raw Material."** The
  rules engine extracts no expense category from a document — there was
  never a field for it — so the auto-booked expense defaults to the single
  most common bucket for a Pakistani SME's supplier spend and is
  correctable via `PUT /expenses/{id}` afterward.
- **Not live/Docker-verified.** Per the explicit instruction for this
  phase, no Docker image was built or deployed and no live scan/hand-off
  was run against the real stack — see §11.4.

## 5. Data model

```
Expense: id, company_id, category (str, suggested list, not enforced),
         vendor_name (str, free text), vendor_id (uuid, not a FK —
         Vendors Service owns it), invoice_id (uuid, not a FK — Invoice
         Service owns it; unique when set),
         date, amount_pkr, payment_method (bank_transfer/online/cheque/
         cash/card), status (pending/approved/rejected),
         source (manual/invoice), reference_id, created_at, updated_at
```

One table for both creation paths — typed in directly (`source=manual`,
starts `pending`) and auto-booked from a purchase invoice
(`source=invoice`, `invoice_id` set, starts `approved` since it already
passed Invoice Service's own review/validate lifecycle) — the same
single-table-two-paths shape Invoice Service itself uses for
scanned-vs-generated sales invoices.

`invoice_id` carries a unique constraint specifically so `POST /expenses/
book-from-invoice` is idempotent: a retried call for the same invoice
returns the already-booked record instead of creating a duplicate or 409ing.

## 6. Backend — `transactions-service` (port 8003)

New service, `backend/services/transactions-service/`, scaffolded
identically to `vendors-service` (FastAPI + SQLAlchemy async + Alembic +
the shared `finpilot-shared` JWT/tenancy library).

**Expense routes** (`app/api/routes/expenses.py`):
- `GET /expenses/options` — suggested categories/payment methods
- `GET /expenses/categories` — approved-only category breakdown, this
  month by default (the donut chart)
- `GET /expenses/summary` — the 4-card Total/Approved/Pending/Rejected
  summary, this month by default
- `POST /expenses/book-from-invoice` — Invoice Service's hand-off,
  idempotent on `invoice_id`
- `GET /expenses`, `POST /expenses` — list (filterable by category,
  status, date range, vendor search) and manual create
- `PUT /expenses/{id}` — correction (e.g. fixing the default category on
  an auto-booked expense)
- `PUT /expenses/{id}/approve`, `PUT /expenses/{id}/reject`

**Dashboard routes** (`app/api/routes/kpis.py`, prefix `/transactions`):
- `GET /transactions/kpis`
- `GET /transactions/chart/revenue-vs-expenses`
- `GET /transactions/chart/cash-flow` (same underlying monthly data,
  reshaped for the frontend's distinct chart component)

All three read Invoice Service's `GET /invoices/sales/summary` (Revenue
Manager's own aggregate — gained a `totals.today_revenue` field in this
phase specifically for the Dashboard's day-level KPI, since that summary
was otherwise bucketed monthly only) and the new `GET /invoices/status-
summary` (§6.1). A failure there degrades the revenue-derived figures
(`KpiSet.revenue_unavailable`) rather than failing the whole dashboard —
expense-side figures are this service's own data and stay correct
regardless, the same "derive, don't lie about it when derivation fails"
convention `vendors-service`'s `spend_unavailable` already established.

### 6.1 `invoice-service` additions

- **`GET /invoices/status-summary`** — purchase document counts grouped by
  status, one query. Named in the architecture report's own §17 endpoint
  list but never built until this phase needed it for the Dashboard's
  Invoice Status chart.
- **`SalesSummaryTotals.today_revenue`** — additive field on the existing
  sales summary response.
- **`transactions_service_client.py`** — the new outbound call from
  `POST /invoices/{id}/send-to-accounting`, for purchase invoices only.
  Best-effort: logs and continues on failure rather than blocking the
  status flip (see §4).

## 7. Frontend

- **`transactions-service.ts`** (new) — mirrors `vendors-service.ts`'s
  client shape exactly (same `request()` helper, same access-token-getter
  registration pattern in `auth-context.tsx`).
- **`app.expenses.tsx`** (rewritten) — real summary cards, category donut,
  revenue-vs-expenses trend, a filterable/searchable ledger table, an "Add
  Expense" dialog, and inline approve/reject actions on pending rows. A
  `source=invoice` row is labelled "From invoice" rather than looking
  identical to a manual entry.
- **`app.index.tsx`** (rewritten) — every chart and KPI now reads from
  Transactions Service, Invoice Service, or Vendors Service; nothing left
  reads `@/lib/data`. See §3 for the two panels that were replaced rather
  than wired to nonexistent data, and §3's note on why KPI cards no longer
  show a delta arrow.
- **`app-shell.tsx`'s `SearchField`** gained optional `value`/`onChange`
  props (previously a purely decorative, uncontrolled input on every page
  that used it) so Expenses' vendor search could be wired without forking
  the component. Every existing call site is unaffected — both props are
  optional and the field falls back to its old uncontrolled behaviour when
  they're omitted.
- **`invoice-service.ts`'s `SalesSummaryResponse.totals`** and a new
  `getInvoiceStatusSummary()` were added alongside the backend fields they
  read, in the same commit as everything else in this phase.

## 8. Testing

- **transactions-service**: 33 new tests (`test_expense_routes.py`,
  `test_kpis_routes.py`) — CRUD, tenancy scoping, the approve/reject
  lifecycle, category/summary math (approved-only, this-month bounds),
  `book-from-invoice`'s idempotency and payment-method mapping, and the
  KPI/chart endpoints' degrade-not-fail behaviour when Invoice Service is
  patched to fail.
- **invoice-service**: +5 `TestStatusSummary`, +3
  `TestSendToAccountingBooksAnExpense` (purchase books, sale does not,
  Transactions Service being down does not block the status flip), +2
  `today_revenue` bucketing tests, +1 existing empty-summary assertion
  updated for the new field.
- **gateway**: routing tests updated — `/api/v1/transactions` and
  `/api/v1/expenses` moved from the "not built yet" parametrized list to
  the "built, requires auth" one.
- Frontent: `tsc --noEmit` clean across the whole project.

## 9. Rollout / sequencing

Not deployed in this pass — see §11.4. When it is: build the
`transactions-service` image, bring up `transactions-service-db` +
`transactions-service` in `docker-compose.yml` (both already added),
rebuild `invoice-service` (for the status-summary endpoint, the
`today_revenue` field, and the new outbound client) and `gateway` (for the
new routes), then live-verify: adding/approving/rejecting an expense,
sending a purchase invoice to accounting and confirming an Expense
appears, and confirming the Dashboard's KPIs/charts populate.

## 10. Encountered and fixed during the build

A genuine Pydantic v2 footgun, not a design decision: every schema in
`transactions-service/app/schemas/expense.py` has a field literally named
`date`. `date: Optional[date] = None` (a field with a default, annotated
with the identically-named type) silently resolves to `NoneType` instead
of `datetime.date` — confirmed with a minimal repro before concluding it
wasn't a typo elsewhere. Fixed by importing the type under an alias
(`from datetime import date as _date`) and annotating with `_date`
throughout that file, rather than renaming the field away from the
architecture report's own "date" column name.

## 11. Implementation notes

### 11.1 What actually landed

Everything in §5-§8 above, plus the gateway routing/config wiring and the
`docker-compose.yml` service definitions for `transactions-service` and
`transactions-service-db`.

### 11.2 Deviations from the original architecture report

Documented inline throughout (§1, §3) rather than repeated here: no Revenue
table, no channel breakdown, no due-date payables model, no delta arrows,
Employee Salaries derived from Expense data instead of a payroll system.

### 11.3 Explicitly not done in this pass

- No Docker build, no deployment, no live verification against the running
  stack (§4, §9) — this was an explicit constraint for this phase, not an
  oversight.
- `bun run lint` was not run — only `tsc --noEmit`.
- No export functionality behind the Expenses page's "Export" button
  (pre-existing, decorative — inherited from the original dummy-data page
  and left as-is, same status as several other "Export" buttons elsewhere
  in this app).

### 11.4 Verified — locally only

- **transactions-service**: 33/33 tests passing (new service, `pytest tests/`).
- **invoice-service**: 125/125 tests passing, including the 11 new
  status-summary/booking-hook/today_revenue tests this phase added.
- **gateway**: 42/42 tests passing, including the routing-table updates.
- **vendors-service**: 41/41 tests passing (untouched by this phase — run
  as a sanity check that nothing in the shared gateway/routing changes
  affected it).
- **Frontend**: `tsc --noEmit` clean across the whole project, including
  the two rewritten routes, the new `transactions-service.ts` client, and
  the `SearchField` extension.
- **Not run**: slack-connector and email-connector's suites currently show
  pre-existing failures in `test_sync_start*.py` (window-days/cursor
  tests) unrelated to anything touched in this phase — neither service's
  code was modified here. Flagged rather than silently ignored; worth a
  separate look before trusting those two suites' green status.
