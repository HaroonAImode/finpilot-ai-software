# Saved Records — categorized cashbook workspace redesign

**Status:** Design → implementing in 2 phases (Phase 1: Invoice Service
backend; Phase 2: frontend). Written before implementation, per the user's
explicit instruction on this feature.

## 1. Summary

The user shared a real petty-cash cashbook (`June Cash book..xlsx`,
STIXOR TECHNOLOGIES format) as the reference for what "Saved Records" should
feel like: a company's petty-cash spend, grouped under category headers
(Office Entertainment, Employee Care, Office/Misc Supplies, Office Repair
and Maintenance, Vehicle Running and Maintenance, Salary, etc.), each with
its own running total, backed by the physical receipt/bill/invoice for every
line.

Today's `/app/records` page (`frontend/src/routes/app.records.tsx`) is a
full-width, flat, spreadsheet-style grid over Invoice Service's validated /
sent-to-accounting invoices — every column editable inline, no grouping, no
image, no per-category total, no PDF export. It is functionally a raw table
dump, not a cashbook.

This redesign turns it into an actual categorized ledger, without inventing
a new backend service or a second source of truth: it stays on Invoice
Service's own `invoice` table (the same "derive, don't duplicate" reasoning
already used for Transactions Service's Revenue and Vendors Service's
vendor-spend), adding exactly one new column (`category`) plus three new
read endpoints and one PDF endpoint.

**The real workflow this supports** (the user's own words): documents keep
arriving via Slack/Email/browser upload/manual scan → the AI Scanner extracts
them into an `Invoice` row → once validated, that row is the cashbook entry.
Nothing about *how* invoices are created changes; this phase is entirely
about what happens to a validated one afterward.

## 2. Goals

1. **Categorization.** Every purchase invoice can carry a `category` — a
   suggested-not-enforced list modeled directly on the uploaded cashbook's
   own vocabulary (§4), the same "suggestion, not an enum" convention this
   codebase already uses for HR roles/departments and vendor categories.
2. **A split workspace, not a wall of a table.** Left: a compact,
   category-grouped list (small thumbnail + particulars + date + amount per
   row). Right: the selected record's full detail and a large preview of
   its source document. Nothing spans the full page width doing nothing
   with the space.
3. **Instant visual context.** Every row shows a small thumbnail of its
   linked receipt/invoice/bill. Hovering a row shows a larger quick preview
   immediately (no click needed just to check "is this the right one").
   Clicking a row loads the full document into the right-hand panel.
4. **Numbers that are exactly right, always.** Category and grand totals
   are never re-summed in the browser from whatever page of rows happens to
   be loaded — they come from one backend SQL aggregate
   (`GET /invoices/categories/summary`), the same pattern Vendors Service's
   `vendor-spend` and Reports Service already use for cross-service totals.
   A total shown on screen is always the total the database actually holds.
5. **A real report per category.** `GET /invoices/categories/{category}/report.pdf`
   — company-branded, the category's entries as a table with an accurate
   total row, followed by every entry's actual receipt/invoice image as a
   labeled appendix page. This is a document someone could hand to an
   accountant, not a debug dump.

## 3. Non-goals / explicitly deferred

- **No petty-cash fund ledger (Opening Balance / Addition / Closing Cash).**
  The uploaded cashbook tracks a physical cash float — money handed to a
  custodian, topped up, spent down, reconciled to a closing balance. That is
  a genuinely different model (a `PettyCashFund` with deposits and a running
  balance) than "a list of invoices," and building it is a separate feature,
  not a page redesign. Flagged here so it is a deliberate omission, not a
  missed requirement — worth its own spec if the user wants it next.
- **No change to how invoices are extracted or scored.** The user was
  explicit that OCR/extraction accuracy (vendor, items, qty, amount, date)
  is the *next* phase, on the Scanner page, not this one. This phase only
  changes what a saved record looks like after it already exists.
- **No bulk multi-row inline editing.** Today's grid lets someone edit many
  rows at once and "Save N changed rows" in a batch. The new list+detail
  layout edits one selected record at a time in the right panel — a
  deliberate UX trade-off for the cleaner split view the user asked for, not
  an oversight.
- **Sales invoices (Revenue Manager) are untouched.** `category` and this
  whole redesign apply to purchase invoices only — a sale has no "petty cash
  category," it has a customer and line items, and already has its own page.

## 4. Category vocabulary

`SUGGESTED_CATEGORIES` on Invoice Service, mirroring the uploaded cashbook's
own headings plus the handful of common SME purchase categories the
cashbook doesn't happen to use this month (so the list is not overfit to one
company's one month of data):

```
Office Entertainment, Employee Care, Office / Misc Supplies,
Stationary & Others, Employee Training & Education, Legal & Professional,
Office Repair & Maintenance, Vehicle Running & Maintenance,
Utilities, Rent, Marketing, Salaries & Wages, Raw Material,
Fuel & Transport, Travel, Other
```

Free-text column (`String(100)`, nullable), not a DB enum — same reasoning
as every other `SUGGESTED_*` list in this codebase: a real SME's categories
never fit a fixed set, and typing a new one must not require a migration.
`NULL` groups under "Uncategorized" everywhere this is displayed or
aggregated, rather than being silently dropped.

## 5. Backend changes (Invoice Service)

- **Migration**: add `category VARCHAR(100) NULL` to `invoice`.
- **`Invoice.category`** + `SUGGESTED_CATEGORIES` tuple (`models/invoice.py`).
- **`InvoiceUpdateRequest.category`**, **`InvoiceResponse.category`**,
  **`InvoiceListItem.category`** — same optional/model_fields_set PUT
  convention as every other correctable field here.
- **`GET /invoices/options`** → `{categories: [...]}`, same shape as HR
  Service's/Settings'/Vendors' own options endpoints.
- **`GET /invoices/categories/summary`** → `[{category, count, total}]`,
  one `GROUP BY category` query scoped to `type=purchase` and (by default)
  `status IN (validated, sent_to_accounting)` — the two states that count as
  "in the cashbook." This is the single source of truth every total shown
  on the page reads from; the row list itself is display data only.
- **`GET /invoices/categories/{category}/report.pdf`** — new
  `app/services/category_report_pdf.py` (ReportLab, same
  `_logo_flowable`/`ImageReader`-for-measurement-only pattern already
  proven in `sales_pdf.py`): company branding header (best-effort via
  `settings_service_client`, degrades to plain if unreachable — identical
  fallback contract to the sales PDF), an entries table (date, particulars,
  payment method, amount) with a totals row from the same aggregate query
  above (never a Python-side re-sum), then one appendix page per entry
  showing its actual source image — the original bytes directly if it is
  already an image, or `render_invoice_page` (already used by
  `GET /{id}/page/{n}`) for a PDF source — labeled with that entry's date,
  particulars and amount so the appendix page is traceable back to its table
  row without guesswork.
- `category` also flows through `POST /invoices/{id}` create paths already
  in place (scanner correction flow) — it is simply another correctable
  field, not a new lifecycle state.

## 6. Frontend redesign (`app.records.tsx`)

**Layout**: a two-pane workspace (`grid-cols-[minmax(360px,420px)_1fr]` on
`lg+`, stacked on mobile) replacing the current full-width table.

**Left pane** — compact, scrollable, category-grouped:
- Search field (existing `SearchField` component) filtering by
  vendor/particulars/invoice number.
- One collapsible section per category (plus "Uncategorized" last), each
  section header showing the category name, entry count, and its accurate
  total from `categorySummary`, plus a small "Download PDF" icon button
  that calls the new report endpoint.
- Each row: a small square thumbnail (`GET /{id}/page/0` — always a PNG
  regardless of whether the source was an image or a PDF, so one thumbnail
  code path covers both), particulars (vendor name, falling back to invoice
  number), date, and the amount right-aligned. Hovering the thumbnail/row
  shows an enlarged quick preview via a Tooltip with rich (image) content —
  no separate hand-rolled popover component needed. Clicking the row selects
  it.

**Right pane** — the selected record:
- The full document preview. `DocumentPreviewBody` itself was not reused
  directly: it resolves its fetcher via `getSource(document.source)` against
  `DOCUMENT_SOURCES`, the same array `/app/documents` iterates to render one
  column per source — adding `"invoice"` there would grow that page an
  unwanted column, not just unlock a preview. Instead, a small dedicated
  `InvoiceDocumentPreview` component (`components/records/`) follows the
  same proven fetch-bytes → object-URL → render-by-mimetype shape, scoped to
  the two real cases a scanned invoice ever is (image or PDF) rather than
  `DocumentPreviewBody`'s fuller image/pdf/html/docx matrix. This is the
  literal "click on that then image keep appearing as a preview" requirement.
- Below the preview: the record's editable fields (vendor, category, date,
  amount, payment method, status) in a proper label/value detail form —
  not spreadsheet cells — with Save/Revert, matching the validation rules
  the existing grid already enforces (numeric fields, the two allowed saved
  statuses).
- Nothing selected: an overview — grand total plus a per-category bar
  (reusing `ChartCard`/`HorizontalBars`, the same charting components every
  other page already uses) instead of a blank panel.

**New lib additions**: `frontend/src/lib/invoice-service.ts` gains
`invoiceCategoryOptions()`, `getCategorySummary()`,
`fetchCategoryReport(category): Promise<Blob>` (same auth-via-fetch pattern
as `fetchInvoiceContent`), and `category` on the `Invoice`/`InvoiceListItem`/
`InvoiceUpdatePayload` shapes.

## 7. Accuracy guarantee

Every total rendered on this page — per-category and grand — is sourced
from `GET /invoices/categories/summary`, a single `SUM()`/`COUNT()` SQL
aggregate run against the full matching row set in the database, independent
of pagination, independent of what the left pane happens to have fetched or
rendered. The frontend never adds up a list of numbers itself to produce a
total shown as authoritative. The same aggregate query backs the PDF
report's totals row, so the on-screen total and the downloaded report's
total are, by construction, the same number.

## 8. Testing

- Backend: category persists through create/update; `/options` returns the
  suggested list; `/categories/summary` aggregates correctly across
  multiple categories and buckets `NULL` as "Uncategorized"; the summary
  excludes non-purchase and non-saved-status rows; the PDF endpoint returns
  a non-empty `application/pdf` response with a correct totals row for a
  fixture with known amounts (asserted the same way `sales_pdf.py`'s tests
  already assert on rendered byte content).
- Frontend: `npx tsc --noEmit` and `npm run build` clean, as with every
  other frontend change this session.
