# Procurement Service — requests, orders, and vendor comparison

**Status:** Implemented (written alongside the build — see §9 for what was
actually verified).

## 1. Summary

Architecture report §5.6's Procurement Service, built close to as
originally specified: purchase requests, purchase orders, and vendor
quote comparison, all real. The two places this deviates from the
architecture report's literal text are both about *where a concept lives*
rather than whether it exists — see §3.

## 2. Goals

- Real purchase-request CRUD with an approve/reject workflow, replacing
  `/app/procurement`'s hardcoded arrays.
- Purchase orders that can be created from an approved request (the
  common case) or standalone, with a delivery-status lifecycle.
- A genuinely useful vendor-comparison feature — recording quotes for a
  specific procurement need, not a company-wide, context-free list.
- A **derived**, always-accurate timeline for a request's lifecycle,
  rather than a separately-authored steps table that could drift from
  what a linked purchase order actually says.

## 3. Deviations from architecture report §5.6

- **`PurchaseRequestStatus` is `{pending_approval, approved, rejected}`,
  not the architecture report's `{Pending Approval, Approved, Delayed,
  Cancelled}`.** "Delayed" and "Cancelled" both describe something going
  wrong with a *delivery*, which only exists once a `PurchaseOrder` does.
  Keeping them on the request would mean either a request magically
  becoming "delayed" with nothing to be late about, or genuine order-level
  problems being tracked in two places. Both concepts moved to
  `PurchaseOrderStatus` and the dashboard stats, where architecture report
  §5.6's own `PurchaseOrder` model already puts them ("In Transit/
  Delivered/Cancelled").
- **"Delayed" is computed, not stored.** An order is delayed when it's
  `in_transit` and `expected_delivery` has passed — checked fresh on every
  read (`/orders`, `/orders/{id}`, `/stats`), not a status a human has to
  remember to set and which could silently go stale. This is the same
  "derive rather than duplicate" reasoning already applied to
  `Expense.category="Salaries"` feeding the Dashboard KPI, vendor spend,
  and `Employee.payment_status`.
- **`/{request_id}/timeline` is derived**, not the architecture report's
  own `ProcurementTimeline` table of freeform `{title, detail, date,
  completed}` steps authored separately. Storing that table would let it
  say something different from what actually happened (a PO issued, then
  delivered) — the four steps here are computed from the request's own
  status plus the earliest linked `PurchaseOrder`'s status, so they can
  never drift out of sync with reality.
- **`requester_name` is free text, not an `Employee` foreign key.** HR
  Service exists now, but there is no reconciliation queue for this field
  (unlike Vendor Reconciliation for OCR `vendor_name`) — adding one for a
  single display column would be a disproportionate amount of new surface
  area for what the Requester column actually needs: a name to show.

## 4. Non-goals / explicitly not touched

- **No RFQ/bid-solicitation workflow.** `VendorQuote` records a quote a
  human already obtained by whatever means they use today (phone, email,
  a vendor's own portal) — nothing here sends anything to a vendor or
  waits for a response. Same "record it, don't automate soliciting it"
  scope Vendor Reconciliation already applies.
- **No scoring formula.** `VendorQuote.score` is a human's own 0-100
  judgement call, entered directly — not something derived from price,
  delivery, and quality by a rule this codebase would have to defend as
  "the AI's opinion." A real purchasing decision deserves a human's
  reasoning, not an unexplainable weighted average.
- **No cancel-a-request endpoint.** A request can be approved or rejected;
  there is no third "withdraw this" action in v1 — the same scope limit
  Expenses' approve/reject-only workflow already accepted.

## 5. Data model

```
PurchaseRequestStatus: pending_approval, approved, rejected
PurchaseRequest: id, company_id, item_description, department (suggested
                 list, own copy — same convention as every other
                 service's own suggestion list), requester_name (free
                 text), amount_pkr, status, created_at, updated_at

PurchaseOrderStatus: in_transit, delivered, cancelled  (exactly
                     architecture report §5.6's own three values)
PurchaseOrder: id, company_id,
               purchase_request_id (real FK, nullable — both tables live
               in this service's own database; nullable because not
               every order originates from a request),
               vendor_id (uuid, not a FK — Vendors Service owns it),
               vendor_name (snapshot),
               order_date, amount_pkr, expected_delivery, status,
               created_at, updated_at

VendorQuote: id, company_id,
             purchase_request_id (real FK, NOT nullable — a quote always
             compares options for a specific need),
             vendor_id (uuid, not a FK, nullable), vendor_name,
             price_pkr, delivery_estimate (free text), quality_rating
             (free text), payment_terms (free text), score (0-100,
             nullable — "not yet scored" is a real, different state from
             "scored zero"), created_at, updated_at
```

## 6. Backend — `procurement-service` (port 8005)

New service, scaffolded identically to `hr-service`/`transactions-
service`. Four routers, each under a distinct top-level path segment
(`/requests`, `/orders`, `/vendor-comparison`, bare `/stats`), so — unlike
Invoice Service's sales-scanner-before-sales-router ordering rule — there
is no cross-router literal-vs-`{id}` collision to guard against here;
each router only needs its own internal literal-before-`{id}` ordering
(e.g. `/requests/options` before `/requests/{id}`).

**`routes/requests.py`**: `GET/POST /requests`, `GET /requests/options`,
`GET /requests/{id}`, `POST /requests/{id}/approve`, `POST /requests/{id}
/reject`, `GET /requests/{id}/timeline`.

**`routes/orders.py`**: `GET/POST /orders`, `GET /orders/{id}`, `PUT
/orders/{id}/status`. Creating an order from a `purchase_request_id`
checks that request is `approved` in this company first (404 if it
doesn't exist, 409 if it isn't approved yet).

**`routes/vendor_comparison.py`**: `GET/POST /vendor-comparison` (list
optionally scoped to one `purchase_request_id` via a query param), `PUT
/vendor-comparison/{id}` (mainly for scoring). Sorted score-descending
then price-ascending, so an unscored quote still shows up sensibly
ordered by price rather than randomly.

**`routes/stats.py`**: `GET /stats` — see §3 for what each figure means.

## 7. Frontend

- **`procurement-service.ts`** (new) — mirrors the other service clients'
  shape.
- **`app.procurement.tsx`** (rewritten) — real stat cards, a "New
  Request" dialog, inline approve/reject on request rows, a "New Order"
  dialog (optionally sourced from an approved request), an inline
  delivery-status selector per order (with a "Delayed" badge when the
  computed flag is true), an "Add Quote" dialog, and inline score editing.
  Clicking a request row selects it (synced to `?request=<id>`, the same
  deep-link convention Revenue Manager's `?invoice=<id>` established) and
  drives both the Status Timeline panel and the Vendor Comparison table —
  the dummy page's own layout implied exactly this relationship (both
  panels referenced "PR-2041" specifically) without any way to actually
  select a different request.

## 8. Testing

- **procurement-service**: 44 new tests across four files — request CRUD/
  approve-reject/timeline derivation (including the multi-step timeline
  once an order exists), order creation-from-approved-request guards, the
  computed `delayed` flag's three cases (in-transit-and-late,
  in-transit-and-on-time, delivered-and-late-is-not-delayed), quote
  scoping/sorting/scoring, and every stats figure.
- **gateway**: routing tests updated — `/api/v1/procurement` moved from
  the "not built yet" list to the "built, requires auth" one.
- Frontend `tsc --noEmit` clean.

## 9. Verified — locally only (no Docker build, no deploy)

- **procurement-service**: 44/44 tests passing (new service).
- **gateway**: 44/44 tests passing (was 43).
- **hr-service**: 25/25, **transactions-service**: 37/37, **invoice-
  service**: 125/125 — all re-run as a sanity check; none was touched in
  this phase.
- Frontend `tsc --noEmit` clean across the whole project.
- **Not run**: no Docker build/deploy, no live procurement flow against a
  real stack — explicit constraint for this phase, same as every service
  built this week.
