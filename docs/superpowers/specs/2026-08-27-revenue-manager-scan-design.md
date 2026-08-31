# Revenue Manager — Scan-to-Revenue Design Spec

*Prepared: 2026-08-27*
*Status: **Implemented** — built the same day, following this spec as written. See §11 below for what actually landed, what changed from the plan, and how it was verified.*

## 1. Summary

The **Revenue Manager** page (`frontend/src/routes/app.revenue.tsx`) is today a
static mock built entirely on dummy data (`extractedSale`, `salesLog`,
`revenueSources` in `src/lib/data.ts`). This spec makes it a real feature that
mirrors the **AI Invoice Scanner** (`app.scanner.tsx`): a user drops a sales
document — a sales invoice they issued, a POS receipt, a delivery challan — or
pulls one from Slack/Email, and the same OCR + rules-based extraction pipeline
that powers the purchase scanner extracts customer, line items, tax and totals
for review, then persists a revenue record with a preview, a scanned-sales list,
and revenue charts.

This **reverses an earlier explicit decision** recorded in
`backend/services/invoice-service/app/api/routes/scanner.py` and
`docs/documents-and-vendors-plan.md` §1a: "sales invoices are generated, not
uploaded." That decision stays true for the existing **Invoice Generator**
(authoring a sales invoice by typing it). Scan-to-revenue is an *additional*
creation path for `type=sale` invoices, not a replacement — an SME's real sales
records arrive as documents from many systems, and scanning them is a real
workflow.

## 2. Goals

- A Revenue Manager page with visual and interaction parity to the Invoice
  Scanner: drag/drop + Browse + Slack + Email upload, a bounding-box document
  preview, an editable extracted-fields panel with per-field confidence, a
  line-item table with arithmetic checks, a scanned-sales list, and real
  revenue charts.
- Reuse the **exact** existing extraction pipeline: `invoice-service` →
  `ai-engine` `/api/v1/ai/ocr/extract` (LiteParse → PaddleOCR → Tesseract →
  `invoice_extraction` rules engine). No changes to `ai-engine` or `paddleocr`.
- Slack and Email connectors can send a discovered file "to Revenue" exactly
  the way they already send "to Scanner".
- No database migration — the `invoice` table already carries every column
  needed.

## 3. Non-goals / explicitly not touched

- `app.scanner.tsx`, `scanner.py`, and the purchase `build_invoice` path — the
  purchase scanner's behavior stays byte-for-byte identical.
- `ai-engine`, `paddleocr`, and the `ocr` / `invoice_extraction` libraries.
- The Invoice Generator authoring endpoints (`POST /invoices/sales/`, its
  `GET`s, `/pdf`).
- Any other service (Auth, Gateway routing, Documents, Vendors, Transactions).
- "Saved Records" (`app.records.tsx`) — scanned sales do not appear there in v1.

## 4. v1 limitations (deliberate)

- **No `known_customers` fuzzy matching.** The purchase scanner passes
  `known_vendors` to the rules engine for fuzzy vendor matching. The equivalent
  for sales would need an additive `known_customers` param on `ai-engine`, which
  is out of scope (AI Engine untouched). `customer_name` is still extracted as a
  field, just not reconciled against past customers.
- **"Post Revenue" is a local status flip.** Transactions Service does not exist
  (architecture report §11b). This reuses the existing
  `POST /invoices/{id}/send-to-accounting`, which is itself a local flip today —
  identical to the purchase side's "Send to Accounting".
- **Charts and list are scanned-sales-only.** Authored (generated) sales
  invoices are excluded from Revenue Manager's list and summary. They remain
  reachable through the Invoice Generator surface.

## 5. Data model

No migration. Scanned sales are `Invoice` rows:

| Column | Value for a scanned sale |
|---|---|
| `type` | `sale` |
| `extraction_source` | `pdf_text` or `ocr` (from the extraction). **This is the discriminator** — authored sales use `generated`. |
| `customer_name` | `extraction.customer_name.value`, falling back to `extraction.vendor_name.value` when the former is empty |
| `vendor_name` | the raw name the rules engine detected — kept so `field_confidence`, `field_locations`, and bounding-box overlay for the counterparty name keep working with no special-casing |
| `raw_extraction_json` | the full untouched AI Engine response (Rule 8.1 audit trail) |
| `s3_key`, `file_hash`, `filename`, `mimetype`, `size` | as for a purchase scan |
| `status` | from the extraction's `review_status` (`processed` / `needs_review` / `needs_review_high_priority`) |

`AIJob` rows are created exactly as the purchase scanner does (the model has no
`type` field and needs none).

**Why `customer_name` is sourced with a `vendor_name` fallback:** the rules
engine's name heuristic finds "the most prominent company name that isn't
obviously a total/label." On a sales invoice that is usually the bill-to
customer, and the engine reports it under `vendor_name`. Its dedicated
`customer_name` detector is a secondary signal. Taking `customer_name` first,
then `vendor_name`, gets the counterparty right in both cases while keeping the
raw evidence and its confidence/bbox intact under the `vendor_name` key.

## 6. Backend — `invoice-service`

### 6.1 New router: `app/api/routes/sales_scanner.py`

Router `prefix="/invoices/sales"`, `tags=["sales-scanner"]`. Included in
`main.py` **before** `sales_router` and `invoices_router` (same literal-path-vs-
`{invoice_id}` ordering reason already documented there). Within the router,
`/scan` and `/scan/{job_id}` are declared before any `/{...}` route.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/invoices/sales/scan` | Multipart `file`. Mirrors `scanner.py::scan_invoice`: size check → `sha256` dedup **scoped to `type==sale`** (returns the existing invoice + a `done` job if the same file was already scanned for this company) → `extract_invoice_data(...)` (no `known_vendors`) → `StorageManager.store_bytes` → `build_invoice(invoice_type=InvoiceType.sale, ...)` → persist → `ScanJobResponse`. Same failure handling (502 on AI Engine error, 502 on storage error, `AIJob` marked `failed`). |
| `GET` | `/invoices/sales/scan/{job_id}` | Mirrors `scanner.py::get_scan_status` verbatim (the `AIJob` lookup is already type-agnostic). |
| `GET` | `/invoices/sales/scanned` | List for the Revenue Manager table. `where type==sale AND extraction_source != 'generated' AND company_id==…`, optional `status` filter (comma-separated, same parsing as `list_invoices`), `skip`/`limit` (defaults 0 / 20, max 100), `order_by created_at desc`. Returns the existing `InvoiceListResponse` shape. |
| `GET` | `/invoices/sales/summary` | Revenue charts + KPIs. Scoped identically to `/scanned`. Returns: `{ "monthly": [{"month": "YYYY-MM", "total": float, "count": int}], "top_customers": [{"customer_name": str, "total": float, "count": int}], "totals": {"revenue": float, "document_count": int, "pending_review": int} }`. `monthly` sorted ascending by month; `top_customers` sorted descending by `total`, capped at 5, `customer_name` null/blank grouped as `"Unattributed"`. Aggregated in Python (row count is bounded by scanned-doc volume, and the service's test suite runs on SQLite — same reasoning as `invoices.py::unlinked_vendor_groups`). `pending_review` counts `needs_review` + `needs_review_high_priority`. |

### 6.2 `app/services/invoice_builder.py`

`build_invoice` gains `invoice_type: InvoiceType = InvoiceType.purchase` as a
keyword-only param. Default preserves the current purchase mapping exactly. When
`invoice_type is InvoiceType.sale`:

- `invoice.type = InvoiceType.sale`
- `invoice.customer_name = _str_or_none(extraction.customer_name.value) or _str_or_none(extraction.vendor_name.value)`
- `invoice.vendor_name` is still set from `extraction.vendor_name.value` (unchanged line)

Everything else (items, confidence blob, status map, S3 metadata) is identical.

### 6.3 `app/main.py`

Add `from app.api.routes.sales_scanner import router as sales_scanner_router`
and `app.include_router(sales_scanner_router, prefix="/api/v1")` **before** the
`sales_router` include.

### 6.4 Schemas

Reuse `ScanJobResponse`, `InvoiceResponse`, `InvoiceListResponse`,
`InvoiceListItem` from `app/schemas/invoice.py` unchanged. Add one new schema
module `app/schemas/sales_summary.py` (`MonthlyRevenuePoint`,
`TopCustomerPoint`, `SalesSummaryTotals`, `SalesSummaryResponse`).

### 6.5 Reused unchanged (type-agnostic already)

`GET /invoices/{id}`, `PUT /invoices/{id}`, `POST /invoices/{id}/validate`,
`POST /invoices/{id}/send-to-accounting`, `GET /invoices/{id}/content`,
`GET /invoices/{id}/page/{page_number}`.

## 7. Backend — connectors (additive, backward-compatible)

### 7.1 `scanner_bridge.py` (slack-connector **and** email-connector)

`send_file_to_scanner(settings, file_obj, company_id, *, invoice_type: str = "purchase")`:

- `invoice_type == "purchase"` → POST to `…/api/v1/invoices/scan` with
  `data={"type": "purchase"}` — **unchanged from today**.
- `invoice_type == "sale"` → POST to `…/api/v1/invoices/sales/scan` (no `type`
  form field needed; the endpoint is sale-only).

Same minted `internal_service_headers`, same timeout, same `ScannerBridgeError`
handling.

### 7.2 Connector route `POST /files/{id}/send-to-scanner`

(`slack-connector/app/api/routes/sync.py`, `email-connector/app/api/routes/sync.py`)

Add optional form param `target: str = "purchase"` (accepts `"purchase"` |
`"sale"`; 400 on anything else). Passes `invoice_type=target` to the bridge.
Default keeps every existing caller working with no change.

### 7.3 Gateway

No change — `/api/v1/invoices/sales/scan` already matches the existing
`/api/v1/invoices` → `invoice_service_url` route (`requires_auth=True`).

## 8. Frontend

### 8.1 `src/lib/invoice-service.ts` — new functions

```ts
scanSalesInvoice(file: File): Promise<ScanJobResponse>          // POST /sales/scan (multipart)
getSalesScanStatus(jobId: string): Promise<ScanJobResponse>     // GET  /sales/scan/{jobId}
listScannedSales(params): Promise<InvoiceListResponse>          // GET  /sales/scanned
getSalesSummary(): Promise<SalesSummaryResponse>                // GET  /sales/summary
```

New exported types: `SalesSummaryResponse` and its parts. Reuse `Invoice`,
`InvoiceStatus`, `ScanJobResponse`, and the existing `getInvoice`,
`updateInvoice`, `validateInvoice`, `sendToAccounting`, `fetchInvoiceContent`,
`fetchInvoicePage` unchanged.

### 8.2 Shared components

- **Reused by import, not modified:** `BoundingBoxOverlay` and
  `InvoiceOriginalPreview` from `src/components/invoices/`.
- **Duplicated** into a new `src/components/invoices/sale-extraction-fields.tsx`:
  the presentational helpers currently *inline* in `app.scanner.tsx` — `Field`,
  `ConfidenceDot`, `DetectedDetails`, `OtherFields`, `formatAmount` /
  `currencyOf` / `currencySuffix` / `confidenceTone` / `confidenceDotTone` /
  `titleCase`. Duplicated rather than extracted so `app.scanner.tsx` is not
  edited. (Alternative on request: extract them to a shared module — a pure
  no-behavior-change refactor of `scanner.tsx`.)

### 8.3 `src/routes/app.revenue.tsx` — full rewrite

Layout mirrors `app.scanner.tsx`:

- `validateSearch` for `?invoice=<uuid>` deep-link (Records-style entry).
- **Left column:**
  - Upload dropzone: drag/drop + **Browse files** + **WhatsApp** (toast stub,
    same as Scanner) + **Slack** + **Email** buttons.
  - `SlackFilesSheet` / `EmailFilesSheet` opened with `scanTarget="sale"`.
  - Preview `<section>`: when the selected invoice has `page_dimensions`, render
    `BoundingBoxOverlay` with page nav + "hover a field to highlight" hint;
    otherwise `InvoiceOriginalPreview`. Confidence pill in `PageHeader` actions.
- **Right column — "Extracted Sale":**
  - Fields grid: **Customer** (bound to `customer_name`; confidence from
    `field_confidence['vendor_name']`, bbox from `field_locations['vendor_name']`),
    Invoice Number, Invoice Date, NTN.
  - `DetectedDetails` (currency, city, country, payment status, document type)
    and `OtherFields` (dynamic fields) — same as Scanner.
  - Products table (`items`) with arithmetic-check icons, editable.
  - Subtotal / Sales Tax (%) / Total summary box with confidence dots.
  - Buttons: **Validate**, **Save**, **Post Revenue** (calls
    `sendToAccounting`) — same enable/disable lifecycle logic as Scanner
    (persist form first, then flip status).
- **Below — "Scanned Sales" table:** invoice #, customer, date, amount, status
  badge; status filter `Select`; row click loads into the panel; empty state.
- **Bottom — charts** from `getSalesSummary()`:
  - KPI row: Revenue captured, Documents scanned, Pending review.
  - **Monthly Revenue** — `VerticalBars` from `charts.tsx` over
    `summary.monthly` (`{ name: month, value: total }`). Add a new `charts.tsx`
    primitive only if `VerticalBars` genuinely does not fit; no new chart
    library either way.
  - **Top Customers** — `HorizontalBars` from `charts.tsx` over
    `summary.top_customers`.
  - Each chart has an explicit empty state ("No scanned sales yet").

### 8.4 `SlackFilesSheet` / `EmailFilesSheet` — `scanTarget` prop

Add `scanTarget?: "purchase" | "sale"` (default `"purchase"`). The send mutation
calls `sendSlackFileToScanner(fileId, scanTarget)` /
`sendEmailFileToScanner(attachmentId, scanTarget)`; button label and success
toast switch to "Send to Revenue" / "Sent to Revenue Manager" when
`scanTarget === "sale"`. Scanner passes nothing → identical behavior.

`sendSlackFileToScanner(fileId, target: "purchase" | "sale" = "purchase")` and
the email equivalent gain the optional arg → `POST …/send-to-scanner` with
`{ target }` as a form field when non-default.

### 8.5 Navigation

`navItems` in `app-shell.tsx` already has
`{ to: "/app/revenue", label: "Revenue Manager", icon: TrendingUp }` — no
change.

## 9. Testing

| Area | Tests |
|---|---|
| `invoice-service` | `tests/unit/test_sales_scanner_route.py` — mirrors `test_scanner_route.py`: happy path persists `type=sale` + `extraction_source` set + `customer_name` populated; duplicate file returns existing; AI Engine error → 502 + failed job; `/scanned` excludes `generated`; `/summary` monthly + top-customer aggregation + `pending_review` count. `test_invoice_builder.py` — add a `invoice_type=sale` case. |
| slack-connector | `test_scanner_bridge.py` — add `invoice_type="sale"` posts to `/invoices/sales/scan`; default still posts to `/invoices/scan` with `type=purchase`. Route test for `target` param + 400 on bad value. |
| email-connector | Same bridge + route tests as slack. |
| frontend | `cd frontend && ./node_modules/.bin/tsc --noEmit` clean; `bun run lint` on changed files. |
| manual / live | Bring up the stack, scan a sales PDF through the Revenue page, pull one from Slack, confirm the record, check the charts populate. |

## 10. Rollout / sequencing

1. `invoice-service`: `build_invoice` param, `sales_scanner.py`, schema, `main.py`, tests.
2. Connectors: bridge + route `target` param, tests (both services).
3. Frontend: `invoice-service.ts` client fns, sheet `scanTarget` prop, `app.revenue.tsx` rewrite.
4. Rebuild the affected containers (`invoice-service`, `slack-connector`,
   `email-connector`), live-verify.

Each step is independently testable; the frontend step is the only one a user sees.

## 11. Implementation notes (added post-build)

Built the same day, following §1-§10 as written, with the deviations and
gaps below recorded rather than left implicit.

### 11.1 What actually landed

- **Backend**: `app/api/routes/sales_scanner.py` exactly as designed —
  `POST /invoices/sales/scan`, `GET .../scan/{job_id}`, `GET .../scanned`,
  `GET .../summary` — registered in `main.py` before `sales_router`.
  `invoice_builder.py`'s `build_invoice` gained the `invoice_type` keyword
  param and the `customer_name`-with-`vendor_name`-fallback logic exactly
  as §6.2 specifies. `app/schemas/sales_summary.py` added as its own
  module, per §6.4.
- **Connectors**: both `scanner_bridge.py`s gained `invoice_type` (default
  `"purchase"`, unchanged for every existing caller); both `send-to-
  scanner` routes gained the `target` Form field with 400 on an unknown
  value, exactly as §7 specifies.
- **Frontend**: `invoice-service.ts` gained `scanSalesInvoice`,
  `getSalesSummary`, `listScannedSales` (`getSalesScanStatus` was written
  but not wired into the page — see §11.2), plus the `MonthlyRevenuePoint`/
  `TopCustomerPoint`/`SalesSummaryResponse` types. `sale-extraction-
  fields.tsx` duplicates `Field`/`ConfidenceDot`/`DetectedDetails`/
  `OtherFields`/the formatting helpers exactly as §8.2 directs —
  `app.scanner.tsx` was not touched. `app.revenue.tsx` is a full rewrite
  mirroring the Scanner's layout: the same four upload buttons (Browse,
  WhatsApp toast-stub, Slack, Email), the same bounding-box/plain preview
  split, an "Extracted Sale" panel with Customer/Invoice Number/Invoice
  Date/NTN, a line-item table with arithmetic-check icons, a Scanned Sales
  table with a status filter, and a KPI row + Monthly Revenue
  (`VerticalBars`) + Top Customers (`HorizontalBars`) — the existing chart
  primitives fit without needing a new one, exactly as §8.3's fallback
  allowed. `SlackFilesSheet`/`EmailFilesSheet` gained the `scanTarget`
  prop from §8.4, switching both the button label ("Send to Revenue") and
  the success/error toast wording when set to `"sale"`.

### 11.2 Deviations from the plan, and why

- **A real gap found while wiring the frontend, not anticipated by the
  spec**: `lib/invoice-service.ts`'s `Invoice` interface — the purchase
  Scanner's own type — was simply missing `customer_name`, even though
  the backend's `InvoiceResponse` schema has carried it since the Invoice
  Generator phase. Never surfaced before because nothing read it. Added
  as part of this work, not a Revenue-Manager-specific type, since the
  Scanner's own `Invoice` type is exactly what a scanned sale's detail
  view uses too (§6.5 — the detail endpoint is already type-agnostic).
- **`getSalesScanStatus` exists but is unused.** `scanSalesInvoice`
  resolves synchronously in one call (mirroring `scanInvoice`'s own
  behavior — the rules engine has no external API call in it, so a
  polling loop has nothing to poll for today), so the page never needs to
  call it. Kept because the backend route it calls
  (`GET /invoices/sales/scan/{job_id}`) is real and tested — this just
  means no current caller exercises that specific client function, the
  same as `getScanStatus`'s own status in `app.scanner.tsx` today (also
  imported, also unused, confirmed by inspection before assuming this
  needed a fix here).
- **Email-connector had zero existing tests for `scanner_bridge.py` or
  its `send-to-scanner` route before this work** — confirmed by grep, not
  assumed. Since this change touches that exact function, full coverage
  was added (`test_scanner_bridge.py`, 8 tests; `test_send_to_scanner_
  target.py`, 3 tests) mirroring slack-connector's own equivalents test-
  for-test, rather than adding only the two new-behavior tests and leaving
  the pre-existing gap in place.

### 11.3 Explicitly not done in this pass

- **No live/Docker verification.** Every existing service was left
  running unmodified and no container was rebuilt for this feature, on
  explicit instruction — verification here is local `pytest` (backend)
  and `tsc --noEmit` (frontend) only. The spec's own §9 "manual / live"
  row (scan a real sales PDF through the running stack, pull one from
  Slack, confirm charts populate) has **not** been done and should be the
  first thing checked before relying on this in the running app.
- **`bun run lint`** (§9's frontend row) was not run — only the type
  check. Worth running before shipping, not blocking for this handoff.
- **No database migration was needed**, confirmed rather than assumed:
  `Invoice.customer_name`, `.extraction_source`, and every column the
  scan-to-revenue path writes already existed from the Invoice Generator
  and OCR-extraction phases.

### 11.4 Verified (local only)

- **invoice-service**: 115 tests passing (was 85) — 25 new in
  `test_sales_scanner_route.py` (scan success incl. the customer_name-
  falls-back-to-vendor_name case, dedup scoped to `type==sale` so a
  purchase invoice sharing a hash is never handed back as a sale,
  AI-Engine/storage failure → 502, `/scanned` excluding both purchase
  invoices and `extraction_source="generated"` authored ones, `/summary`'s
  monthly bucketing/top-5-customers/"Unattributed" grouping/pending-review
  count), 5 new in `test_invoice_builder.py`'s `TestSaleInvoiceType`.
- **slack-connector**: 120 tests passing (was 115) — 2 new in
  `test_scanner_bridge.py` (posts to the sales endpoint with no `type`
  field when `invoice_type="sale"`; unaffected default), 3 new in
  `test_send_to_scanner_target.py` (default/explicit/invalid `target`).
- **email-connector**: 188 tests passing (was 177) — 8 new in a from-
  scratch `test_scanner_bridge.py`, 3 new in `test_send_to_scanner_
  target.py`.
- **Full backend sweep**: 699 tests passing across every service and lib
  touched this session (was 653 before this feature).
- **Frontend**: `tsc --noEmit` clean across the whole project, including
  the new route, the new shared component, and the two updated `*-files-
  sheet.tsx` components.
