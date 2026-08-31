# Reports Service — P&L, Cash Flow, Tax Summary, Sales, Purchases, Balance Sheet

**Status:** Implemented (written alongside the build — see §9 for what was
actually verified).

## 1. Summary

Architecture report §5.9's Reports Service, built with two deliberate,
documented departures from its literal text: generation is synchronous
rather than a RabbitMQ/Celery job, and Balance Sheet is a simplified
cash-position snapshot rather than a true balance sheet. Every other
report type (Profit & Loss, Cash Flow, Tax Summary, Sales, Purchases) is
built close to as specified, computed live from Invoice Service,
Transactions Service, and Settings Service.

## 2. Why generation is synchronous, not queued

Every report here is a handful of HTTP calls to other services plus
in-memory aggregation — nothing OCR-slow, nothing that took minutes the
way PaddleOCR's cold start did. Architecture report §11b's own
long-standing note applies here the same way it has to every other
internal hand-off in this codebase: no RabbitMQ/Celery infrastructure
exists yet, and building one specifically for a job that finishes in
under a second would be solving a latency problem this feature doesn't
actually have. `POST /reports/profit-loss` (and the other five) compute
and persist the result within their own request-response cycle; `GET
/reports/{id}` never needs polling.

A downstream failure (Invoice/Transactions/Settings Service unreachable)
aborts the request with a 502 and creates **no** `Report` row — the same
"the response IS the downstream data" reasoning `invoices.py::
vendor_invoices` already applies, rather than persisting a `failed`
status a caller would have to notice and retry.

## 3. Why Balance Sheet is a simplified cash-position snapshot

A real balance sheet needs fixed assets, accounts receivable/payable,
loans, and owner's equity — none of which exist anywhere in this
codebase. Rather than either refusing to build the endpoint or fabricating
zero-value line items for facts this system cannot know (which would be a
false financial claim, not a gap), `POST /reports/balance-sheet` returns
exactly one real, derivable figure: cumulative all-time revenue minus
cumulative approved expenses up to the requested date — the same
approximation Transactions Service's own Dashboard "Cash Balance" KPI
already uses. The response's own `notes` state this limitation plainly on
both the PDF and the Excel export, not just in this document.

## 4. The one generic report shape

Every report type produces the same `ReportPayload` (title, company name,
period label, a list of summary lines, an optional detail table, and
notes) — one pair of renderers (ReportLab for PDF, openpyxl for Excel)
consumes this shape regardless of report type, rather than twelve bespoke
layouts. A report type only needs to know how to become this shape, not
how to lay out a page. See `app/schemas/payload.py`.

`Report.payload_json` stores the computed result as JSON; PDF and Excel
are rendered **on demand** at download time from that stored JSON, not
pre-rendered to a file at generation time. Architecture report §5.9's own
`file_path_pdf`/`file_path_excel` columns are therefore not part of the
`Report` model — nothing about rendering either format is slow enough to
justify persisting bytes instead of the structured data they come from,
and it means no MinIO/S3 integration was needed for this service at all.

## 5. Sourcing each report

- **Profit & Loss**: revenue from Invoice Service's sales summary
  (period-scoped — see §6.1), approved expenses and their category
  breakdown from Transactions Service.
- **Cash Flow**: the same two figures, reshaped as inflow/outflow with a
  month-by-month table built by pairing the sales summary's own monthly
  buckets against every approved expense in the period (paginated through
  Transactions Service's `/expenses/` list, not a single "give me
  everything" call — see `transactions_service_client.py`).
- **Tax Summary**: revenue and approved expenses as above, combined with
  Settings Service's actual configured GST/withholding rates — a
  simplified estimate, explicitly labelled as not an FBR filing and not
  professional tax advice (real GST law's input-tax-credit and
  category-specific withholding rules are not modelled).
- **Sales Report**: Invoice Service's sales summary with a large
  `customer_limit` (see §6.1) for a full customer breakdown, not the
  Dashboard's own top-5-capped version. Grouped by customer, not channel —
  this system has no sales-channel concept, the same substitution the
  Dashboard's own charts already made.
- **Purchase Report**: Invoice Service's vendor-spend, period-scoped (see
  §6.1). Only invoices already linked to a Vendors Service record are
  counted — an unreconciled OCR vendor name has no vendor to attribute
  spend to, the same limitation Vendors Service's own top-vendors chart
  already has.
- **Balance Sheet**: see §3.

### 6.1 Invoice Service additions

Two existing endpoints gained optional, independently-defaultable
`date_from`/`date_to` query parameters (backward compatible — omitting
both preserves today's all-time behaviour exactly, confirmed by every
pre-existing test still passing unchanged):

- **`GET /invoices/sales/summary`** — filtering happens against the same
  resolved `bucket_date` the monthly chart already computes (invoice_date,
  falling back to the scan date), not a raw SQL `WHERE invoice_date`, so a
  document that only has a date via that fallback is included or excluded
  consistently with how it already appears on the monthly chart. Also
  gained `customer_limit` (default 5, unchanged for existing callers) so
  Sales Report can request a full customer breakdown instead of the
  Dashboard's top-5 cap.
- **`GET /invoices/vendor-spend`** — a plain SQL date-range filter on
  `invoice_date`; a row with no `invoice_date` at all is excluded once
  *either* bound is given, since a report cannot honestly place undated
  spend inside a period.

Balance Sheet needs an **open-ended** lower bound ("all-time up to this
date") that Transactions Service's `/expenses/summary` and `/expenses/
categories` don't support directly — those fall back to "this calendar
month" unless *both* bounds are given (existing behaviour, built for
their own primary caller, not changed for this one). Reports Service
works around this with a `date(2000, 1, 1)` sentinel as the lower bound
rather than changing that endpoint's default semantics.

## 6. Non-goals / explicitly not touched

- **No RabbitMQ/Celery** — see §2.
- **No real balance sheet** — see §3.
- **No PDF/Excel caching or pre-generation.** Every download re-renders
  from the stored JSON; for a report this small, that's cheaper than the
  invalidation logic caching would need.
- **No scheduled/recurring reports.** Every report is generated on demand
  by an explicit request.

## 7. Backend — `reports-service` (port 8014, reassigned from §5.9's
original 8008 — taken by PaddleOCR)

New service, scaffolded identically to every other service built this
week, plus `reportlab` and `openpyxl` as its two report-specific
dependencies. One router (`routes/reports.py`): six `POST` creation
endpoints, `GET /reports` (list, filterable by type), `GET /reports/{id}`,
`GET /reports/{id}/pdf`, `GET /reports/{id}/excel`.

`app/services/report_generator.py` holds the six compute functions — the
actual business logic, kept separate from the route layer so it can be
unit-tested against mocked upstream clients without spinning up a
`TestClient` at all.

## 8. Frontend

- **`reports-service.ts`** (new) — mirrors the other clients' shape, plus
  a `Blob`-returning variant for the two download endpoints (same pattern
  `invoice-service.ts`'s `fetchSalesInvoicePdf` already established:
  fetch as a Blob with the auth header attached, then `URL.
  createObjectURL` + a programmatic `<a download>` click — a plain
  `<a href>` can't carry an Authorization header).
- **`app.reports.tsx`** (rewritten) — six real report cards, each with its
  own period (or as-of-date) inputs, a Generate button that creates the
  report and shows its first few summary lines inline, and PDF/Excel
  buttons that stay disabled until a report has actually been generated.

## 9. Verified — locally only (no Docker build, no deploy)

- **reports-service**: 40/40 tests passing (new service) — computation
  correctness for all six report types against mocked upstream data
  (`test_report_generator.py`), route-level creation/listing/tenancy/
  404s/502-on-failure (`test_report_routes.py`), and PDF/Excel byte-
  signature smoke tests (`test_renderers.py`).
- **invoice-service**: 130/130 tests passing (was 125 — the 5 new tests
  for `sales/summary`'s and `vendor-spend`'s date-range/`customer_limit`
  additions).
- **gateway**: 47/47 tests passing (was 45 — the new `/api/v1/reports`
  route).
- **settings-service**: 17/17, **procurement-service**: 44/44, **hr-
  service**: 25/25, **transactions-service**: 37/37 — all re-run as a
  sanity check, untouched by this phase.
- Frontend `tsc --noEmit` clean across the whole project.
- **Not run**: no Docker build/deploy, no live report generation against
  a real stack — explicit constraint for this phase, same as every
  service built this week.
