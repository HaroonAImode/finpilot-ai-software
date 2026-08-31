# Documents Service, Records Grid & Vendors Service — Build Plan

Coovers the work that follows the Invoice Service phase (`docs/invoice-ocr-plan.md`
Phase 3). Three build streams, in the order they are being built, plus the
documentation debt each one closes.

Written **before** the code, and updated as each stream lands — same discipline
the OCR plan uses, so this file records what was actually decided and why, not a
tidied-up story told afterwards.

---

## 0. Correction to the architecture report's status note

The architecture report's "Status update (as of the Email Connector phase)"
section claims only **Gateway, Auth, and Slack Connector** are built, and that
Invoice Service / AI Engine are "planned, not built".

**That is now stale.** As of this phase the following are built, running in
`backend/infra/docker-compose.yml`, and live-verified:

| Service | Port | State |
|---|---|---|
| Gateway | 8000 | Built |
| Auth | 8001 | Built |
| Invoice Service | 8002 | Built — both halves (scanner + Invoice Generator, see §1/§1a) |
| Vendors Service | 8006 | Built — supplier directory (§3) |
| AI Engine | 8007 | Built |
| PaddleOCR | 8008 (internal) | Built — OCR model server, not in the original blueprint |
| Slack Connector | 8010 | Built |
| Email Connector | 8011 | Built |
| Documents Service | 8013 | Built — browser-upload library (§2) |

Still **not** built from the blueprint: Transactions (8003), HR (8004),
Procurement (8005), Reports (8008 in the blueprint — note the port collides with
PaddleOCR's and needs reassigning), Settings (8009), WhatsApp Connector (8012).

RabbitMQ is still not built. Every internal hand-off is a plain HTTP call, as
`scanner_bridge.py` and `ai_engine_client.py` already do.

---

## 1. Invoice Service — what is actually left

Phase 3 delivered the whole **purchase**-invoice path: scan, poll, list/filter,
detail, correct, validate, send-to-accounting, stream the original file, plus the
grounded review UI and the receipt-robustness work of Phases 4-6.

Outstanding, and deliberately deferred rather than forgotten:

1. ~~**Invoice Generator (sales invoices)**~~ — ✅ **built and live-verified.**
   `POST /invoices/sales/`, `GET /invoices/sales/`, `GET /invoices/sales/{id}`,
   `GET /invoices/sales/{id}/pdf`. Details in §1a below.
2. **Arbitrary line-item table columns** — real invoices carry columns this
   pipeline does not model (a Pakistani payment-summary table's `PAYMENT METHOD`
   column, seen live). Today such a column merges into the description. Tracked in
   `docs/invoice-ocr-plan.md`.
3. **`invoice.vendor_id`** — the architecture report's own model says
   `Invoice: ... vendor_id or customer_id`; today there is only a free-text
   `vendor_name`. Closed by §3 below.

### 1a. Invoice Generator — how it was built

The mirror image of the scanner: instead of reading a document and inferring
fields, it records fields a user authored and produces a document. Nothing here
carries a confidence score, because nothing is a guess.

**Shares the `invoice` table**, discriminated by `Invoice.type`. One ledger, one
place to look — rather than a parallel table every future report would UNION.
Consequences handled:

- `GET /invoices/` now takes a `type` filter, defaulting to `purchase`, so
  generated invoices never appear in the Scanner's list (which is about
  documents that were *received*). `?type=all` returns both.
- `/invoices/sales` is mounted **before** `/invoices/{invoice_id}`, or the
  literal path would be parsed as an invoice id — the same ordering the scanner
  router already needs.

**Totals are computed, never accepted from the caller.** Taking a client-supplied
total would let the stored subtotal, tax and total disagree with the lines they
summarise — exactly the inconsistency the scanner's own arithmetic validation
(Rule 5.1) exists to catch on the purchase side.

**Status starts at `validated`**, not `needs_review`: the values were authored,
not inferred, so there is no extraction to second-guess.

**Scan-only columns became nullable** (migration `20260826_01`):
`raw_extraction_json`, `filename`, `size`, `s3_key`, `file_hash`. A generated
invoice has no uploaded file, so these are genuinely inapplicable. Filling them
with sentinels was rejected — a fake `{}` extraction is indistinguishable from a
real scan that found nothing, which Rule 8.1's audit trail depends on being able
to tell apart. NULL means exactly what it says: this did not come from a document.
`customer_name` was added alongside `vendor_name` for the same reason a sales
invoice is billed *to* someone rather than received *from* them.

**PDF via ReportLab**, rendered on demand rather than stored. A stored PDF goes
stale the moment a field is corrected, with no signal that it has. ReportLab
over WeasyPrint/wkhtmltopdf because those need Cairo/Pango or a bundled
Chromium — pure Python keeps the image light and the build platform-independent.

**A bug the tests caught before deployment:** `InvoiceListItem.filename` was
still declared `str`, so listing any sales invoice raised a `ValidationError`.
Response schemas were updated to match the now-nullable columns.

Verified live: totals `2×9800 + 6×3200 = 38,800`, `+17% = 45,396`; a real
2,440-byte PDF starting with `%PDF-1.4`; and list separation (11 purchase,
1 sales) confirmed against the running stack. 64 tests pass (was 47).

### 1b. Internal service-to-service auth — fixed across all three callers

The bug described in §2.2 below was **not** specific to the new Documents
Service: the Slack and Email connectors' `send-to-scanner` were broken the same
way. Both now use `shared.auth.internal_service_headers`, a single
implementation rather than three copies.

Their hardcoded **30-second** HTTP timeout was raised to a configurable 240s
default at the same time. A scan triggers OCR, which on a CPU-constrained host
genuinely takes minutes (measured — see `docs/invoice-ocr-plan.md`), so 30s was
cutting off scans that would otherwise have succeeded.

---

## 2. Documents Service (port 8013) + Saved Records grid

### 2.1 Why a new service rather than a folder in Invoice Service

The Documents page aggregates *sources* behind one `DocumentSource` interface
(`frontend/src/lib/documents.ts`). Slack and Email are connectors; a browser
upload is a fourth source with the same shape but no OAuth and no sync.

It is **not** invoice data: a user uploads contracts, receipts, photos and
statements that may never become an invoice. Putting a general document library
inside the invoice bounded context would entangle two different lifecycles in one
schema. It gets its own service and database, consistent with every other source.

Port **8013** — 8012 stays reserved for the planned WhatsApp connector.

### 2.2 Endpoints

```
POST   /api/v1/documents/upload           multipart, one file
GET    /api/v1/documents/                 list (skip/limit/category), newest first
GET    /api/v1/documents/{id}/content     stream bytes, inline disposition
PATCH  /api/v1/documents/{id}/category    human re-categorisation
DELETE /api/v1/documents/{id}             soft delete
POST   /api/v1/documents/{id}/send-to-scanner
```

`send-to-scanner` mirrors the connectors' own bridge: a server-to-server POST to
Invoice Service's `/invoices/scan`, degrading to a clear error when that service
is down rather than assuming a queue exists.

#### A pre-existing bug this surfaced: internal calls were not authenticated

Live-testing the hand-off returned **401**, not the expected result. Cause: the
connectors' bridges send only `X-Company-ID`, but Invoice Service runs with
`TRUST_COMPANY_HEADER=false` in Docker — correctly, because behind the Gateway
that header is written from *verified* JWT claims, so trusting a raw one would
let any caller name any company. A server-to-server call has no end-user
`Authorization` header to forward, so it was rejected outright.

**This is not new to this service.** The Slack and Email connectors'
`send_file_to_scanner` send the same bare header, so their `send-to-scanner` is
broken the same way in the current compose. Found by running it, not by reading
it — the unit tests passed throughout, because they patch the bridge.

Fixed here by minting a short-lived (5 min) JWT with the shared
`JWT_SECRET_KEY` every service already holds, with `role="service"` and a fixed
non-user `sub` so an audit trail can tell a service-initiated scan from a
person-initiated one. Verified live: 401 → 502-while-AI-Engine-down → 200 with a
real invoice id once it was up.

**The two connectors still need the same fix** — tracked, not silently left.

### 2.3 Deletion is soft, and that is a deliberate choice

`DELETE` sets `deleted_at`; the row stays and the MinIO object is **not** removed.

- This is a financial app: a document that was seen, categorised, and possibly
  turned into a booked invoice should not be able to vanish without trace.
- **An invoice already scanned from a document is never touched.** The invoice
  owns its own stored copy (`Invoice.s3_key`) — deleting the source document does
  not orphan or alter it. Hard-deleting the MinIO object would risk exactly that.
- Every list query filters `deleted_at IS NULL`, so the UI behaves as the user
  expects — the file is gone from view.

Storage reclamation, if it is ever wanted, is a separate retention job that can
reason about what is safe to purge. That is not this phase.

### 2.4 Persistence — the actual bug being fixed

Today a browser upload goes straight into `POST /invoices/scan` and exists only as
an invoice. There is no record of "a file the user uploaded", so the Documents
page cannot show it and the user re-uploads the same file repeatedly. Storing the
upload first, then optionally sending it to the scanner, is what makes the
Documents page a real library.

### 2.6 Frontend — the fourth column ✅ built

`DOCUMENT_SOURCES` in `frontend/src/lib/documents.ts` gained a `browser`
source. Two things the existing abstraction did not anticipate, both fixed
rather than worked around:

- **`connected` assumed every source has `getConnection`.** A browser upload
  has nothing to connect, so the column rendered a permanent "Not connected".
  A source without `getConnection` is now connected by definition.
- **`DocumentSource` had no delete or upload.** Both were added as *optional*
  capabilities, so the connector columns are unchanged — deliberately: a
  connector's documents mirror what is in Slack or a mailbox, so deleting our
  copy would reappear on the next sync and imply we deleted something we did
  not. Only the browser source shows a Delete control.

**Category vocabularies differ between the two systems** — the service stores
a compact lowercase enum, the page filters on Title Case labels, and they
overlap only partially (the service has no "Project Plans"; the page has no
"other"). Mapped explicitly in both directions. A filter the source has no
equivalent for returns *nothing* rather than silently ignoring the filter and
showing everything, which would look like the filter was broken.

The grid went from `lg:grid-cols-3` to `md:grid-cols-2 xl:grid-cols-4` — four
columns of document cards need the wider breakpoint.

### 2.5 Saved Records — spreadsheet-style editing

`/app/records` currently renders a read-only table. It becomes an editable grid:

- Inline cell editing for **vendor, invoice number, date, NTN, subtotal, tax,
  total, payment method, and status**, per the decision taken for this phase.
- Dirty rows are tracked and saved explicitly (no save-on-every-keystroke), so a
  half-typed number never reaches the API.
- Amounts and status are editable **because an admin asked for it**, and both are
  called out here as the higher-risk choices: these are already-validated,
  possibly-booked records.
  - **Status is restricted server-side to `validated ⇄ sent_to_accounting`.** An
    admin may move a record between the two saved states, but the grid is not a
    back door into the extraction lifecycle — a `needs_review` invoice still has
    to go through the Scanner's own guards. `POST /send-to-accounting` keeps its
    409 for un-validated invoices.
  - Every edit still lands in the invoice's own columns; `raw_extraction_json`
    remains the untouched record of what the engine originally read (Rule 8.1).

**✅ Built.** Implementation notes:

- Drafts are held **as strings**, keyed by invoice id. A half-typed `"12."` is a
  valid intermediate state, so coercing on every keystroke would fight the user;
  parsing happens once, on save. Keying by id means a background refetch that
  reorders rows can never land an edit on the wrong record.
- A refetch **never clobbers a row being edited** — only rows with no draft yet
  are seeded from the server.
- A number cell that cannot be parsed is **flagged and blocks the save** rather
  than silently persisting `null`, which would clear a real figure.
- Dirty rows are highlighted, with per-row Save/Revert plus a "Save N changed
  rows" action. `customer_name` and `ntn` were added to the list endpoint so the
  grid is a true spreadsheet over the list, with no per-row detail fetch.

Verified live against the running stack — `200 / 200 / 409 / 422 / 200` for
validated→sent, sent→validated, needs_review→sent (blocked), an out-of-range
status (rejected), and an ordinary field edit on an unreviewed invoice (still
allowed). Status and `customer_name` confirmed to persist by reading back.
5 backend guard tests added; 69 invoice-service tests pass.

---

## 3. Vendors Service (port 8006) — ✅ built

### 3.1 The problem it fixes, which already exists

`Invoice.vendor_name` is free text written from whatever OCR read. There is no
vendor registry, so `scanner.py::_known_vendors()` currently does:

```sql
SELECT DISTINCT vendor_name FROM invoice WHERE company_id = ...
```

Fuzzy vendor matching (Rule 3.4/7.3) therefore matches against *strings scraped
from past OCR output*. Consequences, all real:

- `"ARGENTO NEUE"` and `"A ARGENTO NEUE"` (a genuine logo-glyph misread seen live)
  become two permanent, separate vendors.
- OCR noise enters the match list and then degrades future matching — a feedback
  loop that gets worse with use.
- A vendor cannot exist before it appears on a scanned invoice.
- Category, city, rating, payment terms, NTN and status have nowhere to live.

### 3.2 Endpoints (architecture report §5.7)

```
GET  /api/v1/vendors                list, filter by category/status/city
POST /api/v1/vendors                create
PUT  /api/v1/vendors/{id}           update
GET  /api/v1/vendors/{id}/invoices  invoices from this vendor
GET  /api/v1/vendors/top            top vendors by spend (charts)
```

`Vendor: id, company_id, name, category, city, total_spend_pkr, rating,
payment_terms, status, ntn`

### 3.3 The cross-service question, stated honestly

Vendors is its own service with its own database, per the blueprint. That means
`GET /vendors/{id}/invoices` and `total_spend_pkr` cannot be a SQL join — they
need a call to Invoice Service. This is a real cost of the chosen split and is
accepted deliberately:

- `/vendors/{id}/invoices` proxies to Invoice Service filtered by vendor, the same
  internal-HTTP pattern `ai_engine_client.py` already uses.
- `total_spend_pkr` is **derived, never stored** in the first cut. A stored
  aggregate would drift the moment an invoice is corrected in the Records grid,
  and there is no event bus to keep it honest. Deriving it is slower and correct;
  caching it is a later optimisation with a real invalidation story.

### 3.4 Linking invoices to vendors

`invoice.vendor_id` (nullable FK-by-convention, since it crosses a service
boundary) is added alongside the existing `vendor_name`. `vendor_name` is kept,
not replaced: it is the raw OCR evidence, and Rule 8.1 says the extraction record
is not overwritten by a human's or a matcher's decision.

Backfilling existing invoices means deciding which near-duplicate name strings are
the same vendor. Because OCR noise is precisely what created those duplicates,
this is **not** an automatic merge — it needs a review step. Scoped as its own
task, not smuggled into the migration.

### 3.5 Frontend — `/app/vendors` ✅ built

Switched from `data.ts`'s hardcoded arrays to the real service. Search and a
status filter drive the query; an **Add Vendor** dialog posts to `POST /vendors`
and surfaces the backend's 409 verbatim, since "a vendor with this name already
exists" is exactly what the user needs to hear.

**The page respects `spend_unavailable` rather than rendering zero.** When
Invoice Service cannot be reached, spend cells show "Unavailable" with a warning
icon, the invoice count shows an em dash, and the header's total-spend figure is
omitted entirely rather than summing a column of fabricated zeros. Showing a
real supplier's spend as a confident `PKR 0` would be a false statement, not a
missing one — the same reasoning that put the flag in the API.

The Top Vendors chart filters out vendors with unavailable or zero spend, and
renders an explanatory empty state ("spend appears once invoices are linked to a
vendor from the Scanner") rather than an empty axis. Categories and payment
terms in the dialog come from `/vendors/options`, fetched only when the dialog
first opens — they are suggestions and never change at runtime.

`rating` renders as an em dash when null: unrated and rated-zero are different
statements about a supplier, and the UI keeps that distinction.

### 3.6 Frontend — `/app/invoices` ✅ built

Replaced the local-state-only, print-only mock with the real Invoice
Generator. The compose form and live preview match the backend and the PDF
renderer field-for-field: customer, optional invoice number/date/NTN, line
items, tax rate. **Due date, discount, and notes were dropped** — none of the
three exist on the stored `Invoice` row or in `sales_pdf.py`'s output, so
showing them would promise a document the download does not actually
contain.

**Save, then download — not one combined action.** `POST /invoices/sales`
computes and returns the authoritative totals; the preview panel switches to
showing the server's numbers rather than the client's own arithmetic once
saved, the same reasoning as Vendors' `spend_unavailable`: once a source of
truth exists, the UI defers to it. Editing a saved sales invoice is not
built here — the existing Records grid already does that generically across
both invoice types, so this page only needs create + list + PDF.

PDF download follows the established authenticated-blob pattern (same as
`fetchInvoiceContent`/`document-preview-dialog.tsx`): a plain `<a href>` to
`/pdf` would carry no bearer token and 401, so the client fetches the blob
itself and triggers the save via an object URL.

**Verified live** (`TRUST_COMPANY_HEADER=true` override, removed after): a
two-line invoice (`145,000×1 + 28,000×3`, 17% tax) created via the exact
payload shape the frontend sends came back with `subtotal=229,000`,
`tax_amount=38,930`, `total=267,930` — matching client-side arithmetic
exactly — and the downloaded PDF was a valid 1-page, 2,471-byte document.

### 3.7 Vendor documents — a proposal, not a requirement

Nothing in the architecture report or any plan doc mentions vendor document
upload (contracts, tax certificates, agreements). Searched and confirmed absent.

It is a reasonable feature and the Documents Service above already provides the
storage primitive — a `vendor_id` column on a document would be enough to attach
one. Recorded here as a **proposal awaiting a decision**, explicitly not built on
the assumption that it was always intended.

### 3.8 Vendor Reconciliation ✅ built

The vendor backfill, designed as an actual accounting feature rather than a
migration script — worked through as OODA, since that is how it was actually
decided rather than assumed up front.

**Observe.** Before designing anything, the actual data was queried rather
than guessed at: 14 purchase invoices existed, 13 with `vendor_id` still
NULL, spread across 7 distinct raw `vendor_name` strings. Three of those 13
invoices shared one exact string, `"INVOICE Invoice #: INV-2025-0872"` —
looking like a scanner fallback triggered when vendor detection fails, not
three different documents that happen to agree. Two more raw strings —
`"Receipt"` and `"Bill #565151287 30-day billing cycle"` — were plainly not
vendor names at all, and one, `"muhammad haroon"`, was a personal name from
a misread field. This mattered directly: a naive "one row per invoice"
migration UI would have made an accountant manually dismiss the same wrong
string three separate times, and would have had no way to say "this isn't a
vendor" at all — only link-or-skip.

**Orient.** Two things followed from that. First, grouping has to happen by
the *exact* raw string, in Invoice Service (`GET /invoices/vendor-groups`) —
not fuzzy, and not in Vendors Service — because collapsing three invoices
sharing one bad string into one review action is the actual efficiency win,
and fuzzy grouping *across different* strings is a separate, harder problem
that only Vendors Service can attempt (it alone holds the vendor directory
to compare against). Second, "not a vendor" had to be a first-class,
reversible decision, not a workaround — hence a dedicated ignore-list table
(`ignored_vendor_name`), keyed on the exact string per company, not a
pattern or a vendor id. A pattern-based ignore would be one misconfigured
rule away from swallowing a real vendor's name; an exact-string list is
undone by deleting one row.

**Decide.** Rules-based string similarity (stdlib `difflib.SequenceMatcher`,
`app/services/match.py`), not an LLM or embedding model — the same posture
as the OCR extraction pipeline itself (`docs/invoice-ocr-plan.md`):
deterministic, explainable, no model dependency for a service this small.
Suggestions are ranked but **never applied automatically** — two names can
score high on pure string similarity and still be different real suppliers,
and the cost of a wrong auto-link (spend silently misattributed) is worse
than one extra click. Vendors Service owns the queue endpoint
(`GET /vendors/reconciliation/queue`) because it is the one place that has
both halves — Invoice Service's raw groups and this company's own vendor
directory to score them against — fetched fatally (502 on failure), not
degraded, because the queue *is* Invoice Service's data: an empty result on
failure would tell an accountant "nothing left to reconcile," which is not a
claim to make when the service simply could not ask. This is the same
asymmetry as `GET /vendors/{id}/invoices` in §3.3.

**Act.**
- `GET /invoices/vendor-groups` — every unlinked purchase invoice grouped by
  exact `vendor_name`, with the invoice ids, total, and latest invoice
  number/date per group. Grouped in Python, not `array_agg`: that function
  is Postgres-only and this endpoint's own tests run on SQLite, and row
  counts here are bounded by "invoices nobody has reconciled yet."
- `POST /invoices/bulk-link-vendor` — sets `vendor_id` on every invoice in a
  group in one transaction. Silently skips ids that no longer match the
  caller's company rather than failing the whole batch: the ids were scoped
  from that company's own vendor-groups response moments earlier, so a
  mismatch means something else changed between the two calls, not a client
  bug worth failing loudly over.
- `GET /vendors/reconciliation/queue` — the groups above, minus any ignored
  raw name, each with up to 3 suggested vendor matches (score ≥ 0.55).
- `POST /vendors/reconciliation/link` — link a group to an existing vendor
  (a suggestion, or one found via a searchable picker).
- `POST /vendors/reconciliation/create-and-link` — record a genuinely new
  vendor and link the group in one step. A downstream link failure here
  leaves the created vendor in place rather than rolling it back: the vendor
  record itself is valid and worth keeping regardless of whether the linking
  call succeeded.
- `POST /vendors/reconciliation/ignore`, `GET`/`DELETE
  /vendors/reconciliation/ignored/{id}` — dismiss a raw name as not a
  vendor, list what has been dismissed, and undo one. Listed, not just
  writable, so "ignore" is visibly reversible rather than a one-way door.
- Frontend: `/app/vendor-reconciliation`, its own nav item (after Vendors) —
  a card per raw name showing count/total/sample invoice, one-click buttons
  for each suggestion, a searchable vendor picker (shadcn `Command` +
  `Popover`) for anything the suggestions missed, a "this is a new vendor"
  dialog seeded with the raw name, an "ignore" action, and an "Ignored
  names" popover to review and undo. An empty queue and a failed queue
  fetch are rendered as two visually distinct states — a success banner
  ("All caught up") versus an error banner with Retry — since collapsing
  them would repeat the exact mistake the backend's 502 exists to prevent.

**Verified live** against this project's own data (not fabricated cases):
grouping correctly collapsed the 3-invoice `"INVOICE Invoice #: …"` string
into one group; ignoring `"Receipt"` and the billing-cycle string removed
them from the queue, restoring one brought it back (`ignored_count` tracked
correctly throughout); `create-and-link` on `"ARGENTO NEUE"` produced a real
vendor with `invoice_count: 1` and spend derived live, exactly as §3.3
describes; stopping Invoice Service turned the queue **502**, not an empty
list. 28 new tests (19 vendors-service, incl. `test_match.py`; 9
invoice-service; total suite now 636).


---

## 4. Document cleanup — tagging non-financial documents and deleting them ✅ built

Slack and Email pull in *everything* shared in a synced channel or mailbox —
university assignments, design files, bank statements, project reports —
not just invoices and receipts. Confirmed live against this project's own
account before building anything: 57 email attachments included lab reports,
`.docx` assignments, tire-catalogue images, and bank eStatements alongside
the handful of real invoices. There was no way to tell FinPilot "this one
isn't a financial document" and no way to remove it from view at all.

### 4.1 What already existed vs. what didn't

Investigated before writing any code, because the answer differed sharply by
source:

- **Browser uploads** (documents-service) already had almost exactly this:
  a `DocumentCategory.other` enum value and a real soft-delete
  (`deleted_at`, `DELETE /{id}`).
- **Slack and Email** had neither. Their `File`/`EmailAttachment` tables
  had no delete flag and no delete route at all — confirmed by grep, not
  assumed. Both already had a mutable `category` string column and a
  `PATCH .../category` endpoint, so "mark as Other" needed no schema change
  there; delete needed a real one in both connector services.
- **The frontend already conflated two different meanings under one label.**
  Browser-upload's explicit `other` (a person picked it) rendered as the
  display label `"uncategorized"` — the exact same label Slack/Email show
  when their rules engine simply *couldn't guess*. Fixed: `other` now maps
  to a distinct `"Other"` label, so "a person said this isn't financial" is
  never shown identically to "the classifier gave up."
- **No delete confirmation existed anywhere**, for any source, including
  browser uploads — the Trash icon fired an immediate DELETE with no dialog.

### 4.2 Soft delete, added to Slack and Email

Same pattern as documents-service, not a new one: `deleted_at` on
`File`/`EmailAttachment`, every list/get/content/category/retry route
filtered on `deleted_at IS NULL`, a new `DELETE /files/{id}` on each that
stamps it. Confirmed before writing the migration that neither connector's
upsert-on-resync logic (`discovery.py`'s `_persist_file`,
`persistence.py`'s `persist_attachment`) touches `deleted_at` — both only
update the specific fields they list — so a deleted file stays deleted
across every future sync rather than reappearing.

Deliberately **not** the same as Email's existing `ReviewStatus.rejected`:
rejecting is a pre-download decision on something still sitting in
`needs_review`; this is a post-import "a user no longer wants to see this,"
on something already imported. Conflating the two would make "rejected"
mean two different things depending on when it happened.

No "undelete" endpoint was built for either connector, matching
documents-service's own precedent (it has none either) — a soft-deleted
document is meant to be gone from the user's view, not parked in a recovery
tray.

### 4.3 The delete confirmation dialog, with a real preview

Built as a shared preview-rendering component
(`document-preview-body.tsx`) used by *both* the full preview dialog and a
new compact delete-confirmation dialog — extracted rather than duplicated,
so the two never quietly drift into rendering the same PDF/image/docx
differently. The full dialog was refactored down to owning only its header
chrome (title, category badges, the HTML Page/Source toggle) and footer
(Open-in-source, Download); the fetch-and-render logic moved to the shared
component unchanged.

The confirmation (`delete-document-dialog.tsx`) shows the filename,
category, size, and an actual rendered thumbnail of the document — not
just its name — because the point is letting someone recognise a stray
file before it disappears. Wording is explicit that this is reversible in
spirit, not in mechanism: *"This only removes it from FinPilot — the
original in {source} is untouched, and it won't come back on the next
sync."* Every source routes through one dialog and one mutation, owned by
`SourceColumn`, so a grouped view (Slack's by-channel mode) and the flat
view never end up with two independent delete flows to keep in sync.

### 4.4 Verified live

Both connectors' soft-delete tested against **synthetic, disposable rows**
inserted directly into their databases — not against the account's real
documents, since there is no undelete built for either. Create → visible →
`DELETE` → `204` → `404` on every route that resolves through the scoped
lookup → gone from the list, for both Slack and Email, then the test rows
were hard-deleted afterward to leave no trace.

The frontend was then verified against the account's **actual** documents
(a live session happened to be available in this browser context): Delete
opened the dialog with a working image preview (445×354, fetched via
`blob:` URL exactly as the existing preview dialog does), Cancel closed it
with no request sent, and a real click through to Remove on one of this
session's own throwaway test uploads produced the `DELETE` request, a
"Document removed" toast, and the row disappearing — end to end, not just
the API in isolation. Browser Upload's category badge now reads "Other"
rather than "uncategorized", confirming the label fix landed.

10 new tests (5 slack-connector, 5 email-connector) — slack-connector
110→115, email-connector 172→177, full suite green throughout.

---

## 5. Documentation kept in step

Each stream updates, as it lands:

- `CHANGELOG.md` — added/fixed/changed/verified, newest first. Up to date
  through Vendor Reconciliation (§3.8) as of this writing.
- The architecture report's status note — §0 above.
- `docs/invoice-ocr-plan.md` — anything that changes the extraction pipeline.
- This file — decisions and their reasons, as they are made.
