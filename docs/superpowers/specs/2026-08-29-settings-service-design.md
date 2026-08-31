# Settings Service — company profile, tax configuration, AI automation

**Status:** Implemented (written alongside the build — see §7 for what was
actually verified).

## 1. Summary

Architecture report §5.10's Settings Service, built as originally
specified: three company-scoped singletons — Company profile, Tax
configuration, AI automation toggles — each with its own `GET`/`PUT`
pair. `/app/settings`'s own placeholder text has said since the
Documents/Vendors phase that "NTN, sales tax registration, city, currency
and default tax rate will live here once the Settings service is built" —
this closes that promise, including its explicit "editing is disabled
until then, so nothing you type can be quietly lost" caveat.

## 2. Goals

- Real Company/Tax/Automation persistence, replacing the Company tab's
  dashed-border "not built yet" placeholder and the Automation tab's
  purely decorative, uncontrolled switches.
- Every row created lazily with sensible defaults on first read or write —
  no signup-time provisioning step needed from Auth Service, and no
  "settings not found" error a brand-new company could ever hit.
- Honest treatment of the AI Automation toggles: real, persisted,
  API-backed — but not silently claiming to control pipeline behaviour
  that doesn't check them yet. See §3.

## 3. What the automation toggles actually do today

Architecture report §5.10 names five flags
(`auto_categorize_expenses`/`auto_detect_duplicates`/`auto_insights`/
`smart_vendor_suggestions`/`enabled_ai`) without saying what enforces
them. None of the five gates real behaviour anywhere in this codebase
yet — persisting them now, honestly documented as partly inert, is worth
more than either fabricating enforcement or refusing to build the
feature at all until every consumer exists. Per toggle:

| Toggle | What would use it | Current state |
|---|---|---|
| `auto_categorize_expenses` | A future auto-category heuristic for scanned-invoice expenses | Not built — book-from-invoice always defaults to "Raw Material" (see the Transactions Service design spec) regardless of this flag |
| `auto_detect_duplicates` | Making the Scanner's sha256 dedup check optional | The dedup check (Rule 7.1) runs unconditionally today |
| `auto_insights` | AI Insights cards (architecture report §5.8 part 3) | Not built at all |
| `smart_vendor_suggestions` | Making Vendor Reconciliation's fuzzy-match suggestions optional | Those suggestions run unconditionally today |
| `enabled_ai` | A master kill-switch for AI features | Not enforced — OCR/extraction runs regardless |

The frontend's own toggle descriptions state this plainly rather than
implying each switch already does something. Wiring real enforcement into
any of these is future work that reads this table before acting, not part
of this phase.

## 4. Non-goals / explicitly not touched

- **No signup-time row creation.** Auth Service does not call out to this
  service when a company is created — each singleton is created lazily on
  its own first read/write instead (see §5).
- **`Company.name` is not synchronised with Auth Service's own
  `company_name`.** They are two independent facts that happen to often
  agree — see the model's own docstring for why copying one into the
  other at creation time would be worse (a false claim of
  synchronisation) than leaving the new field genuinely blank until set.
- **No logo upload.** `Company.logo_url` is a plain string field — no
  object storage integration, matching the scope of everything else in
  this phase.
- **Appearance tab's "Compact tables"/"Animated charts" stayed exactly as
  they were** — decorative, uncontrolled, client-only. Architecture
  report §5.10 names Company/Automation/Tax only; inventing a backend
  field for a pure UI-density preference would be scope creep this
  service doesn't need.

## 5. Data model

```
Company:            id, company_id (unique), name, ntn, address, city,
                     logo_url, industry, phone, email, created_at, updated_at
AutomationSettings:  id, company_id (unique), auto_categorize_expenses,
                     auto_detect_duplicates, auto_insights,
                     smart_vendor_suggestions, enabled_ai (all bool,
                     default true), created_at, updated_at
TaxSettings:         id, company_id (unique), default_gst_rate (default
                     18.0 — "the standard Pakistani rate," architecture
                     report §5.10's own words), withholding_tax_rate
                     (default 0.0), filer_status (enum: filer/non_filer —
                     a genuinely fixed two-value FBR classification,
                     unlike vendor/expense category's open suggestion
                     lists), created_at, updated_at
```

Each table is a one-row-per-company singleton (`UNIQUE(company_id)`), not
addressed by its own row id in any URL — `GET/PUT /settings/company`,
`/settings/automation`, `/settings/tax` take no id at all.

## 6. Backend — `settings-service` (port 8009)

New service, scaffolded identically to every other service built this
week. Three routers (`company.py`, `automation.py`, `tax.py`), each under
its own literal `/settings/*` path — no `{id}` pattern anywhere in this
service, so there is no route-ordering hazard to guard against.

Every route shares one `_get_or_create(db, company_id)` helper (one per
router, same shape): fetch the row for this company, and if none exists
yet, insert one with the model's own column defaults and return it. This
is what makes a brand-new company's very first `GET /settings/tax` return
a real, correct 18%-GST row instead of a 404 — there is no separate
"initialize my settings" step for a caller to forget.

## 7. Frontend

- **`settings-service.ts`** (new) — mirrors the other service clients'
  shape.
- **`app.settings.tsx`**: the Company tab's dashed-border placeholder was
  replaced with two real, independently-saved forms (Business Details,
  Tax Configuration); the Automation tab's five switches are now
  controlled, persisted, and save immediately on toggle (the usual
  settings-switch UX, no separate Save button) — each with the honest
  description from §3. Appearance and Connected Apps tabs are untouched.
- **`app-shell.tsx`'s `SearchField` extension** and every other shared
  component from prior phases needed no changes here.

## 8. Verified — locally only (no Docker build, no deploy)

- **settings-service**: 17/17 tests passing (new service) — lazy
  get-or-create defaults, partial updates, persistence across reads, and
  tenancy scoping for all three resources.
- **gateway**: 45/45 tests passing (was 44 — the new `/api/v1/settings` route).
- **procurement-service**: 44/44, **hr-service**: 25/25, **transactions-
  service**: 37/37, **invoice-service**: 125/125 — all re-run as a sanity
  check, untouched by this phase.
- Frontend `tsc --noEmit` clean across the whole project.
- **Not run**: no Docker build/deploy, no live verification against a
  real stack — explicit constraint for this phase, same as every service
  built this week.
