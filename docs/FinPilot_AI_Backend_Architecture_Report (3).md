# FinPilot AI — Backend Architecture & Project Structure
## FastAPI Microservices — Senior Backend Developer Guide
*Prepared: August 12, 2026 · Corrections/status updates added: September 13, 2026*

---

## What I Found in Your Repo (Important — Read This First)

After checking your actual project files, here is the current situation:

**The repository contains both the frontend and the FastAPI backend.**

The frontend is built with React + TypeScript + Vite + TanStack Router. The backend is an
implemented FastAPI microservice stack with PostgreSQL, Redis, MinIO, deterministic OCR and
rules-based invoice extraction. `src/lib/data.ts` remains the frontend fallback/demo dataset;
the live backend and `backend/infra/docker-compose.yml` are the ground truth for current runtime
behaviour.

This document remains the target architecture and implementation guide. Where the current
implementation differs from the original blueprint, the status notes and service documentation
take precedence.

This document tells you exactly how to do that — which services to build, what each one does, what folders go where, and how the frontend talks to the backend.

---

## Status update (as of the Documents/Vendors phase) — read this before trusting any "existing service" claim

Everything above and below this note is the **original target blueprint**, written before any
backend existed. It is still the right design to build towards, but it is not a description of
what runs today. `backend/infra/docker-compose.yml` is the ground truth; this table is what it
currently contains:

| Service | Port | State |
|---|---|---|
| Gateway | 8000 | **Built** |
| Auth | 8001 | **Built** |
| Invoice Service | 8002 | **Built** — both halves: the AI Scanner (purchase) and the Invoice Generator (sales, §5.3). Purchase invoices also carry an optional, suggested-not-enforced `category` (Office Entertainment, Employee Care, Office Repair & Maintenance, etc.), backing the Saved Records page's categorized cashbook view, its per-category totals (one SQL aggregate, `GET /invoices/categories/summary`), and its per-category PDF report — see [`docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md`](docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md). Includes a **two-stage human-in-the-loop review workflow**: (1) Global Header **Needs Review Notification Center** (`NeedsReviewCenter`) with live count badge and an interactive review/edit modal (`InvoiceReviewDialog`) for triaging newly imported receipts/bills from mobile camera, email, slack, or upload before posting; (2) Dedicated **Needs Review Queue** at the bottom of the Saved Records page (`NeedsReviewSection`) with prominent red-accented warning styling ensuring unverified items are clearly isolated from confirmed records; (3) **50% Centered Default Zoom Preview** (`InvoiceDocumentPreview`) so receipts open comfortably scaled and centered without viewport overflow. |
| Vendors Service | 8006 | **Built** — §5.7; spend derived from Invoice Service, never stored. Also owns Vendor Reconciliation (`/vendors/reconciliation/*`) — the accountant's queue for linking OCR `vendor_name` strings to real vendor records, see `docs/documents-and-vendors-plan.md` §3.8 |
| Transactions Service | 8003 | **Built, scoped to Expenses only** — §5.4's Expense CRUD/approve/reject and the Dashboard KPI/trend endpoints. Revenue is deliberately **not** a table here: it stays Invoice Service's own scanned-sales ledger (Revenue Manager), read over HTTP — see [`docs/superpowers/specs/2026-08-29-transactions-service-design.md`](docs/superpowers/specs/2026-08-29-transactions-service-design.md) for why. A purchase invoice's "Send to Accounting" now really does book an Expense here, the real hand-off §5.3's own docstring had promised since before this service existed. |
| HR Service | 8004 | **Built** — §5.5's employee directory and payroll processing. `POST /employees/payroll/process` books a matching "Salaries"-category Expense in Transactions Service, best-effort — see [`docs/superpowers/specs/2026-08-29-hr-service-design.md`](docs/superpowers/specs/2026-08-29-hr-service-design.md). The Dashboard's "Employee Salaries" KPI (Transactions Service) needed **no changes** to pick this up — it already read the "Salaries" Expense category. |
| Procurement Service | 8005 | **Built, close to §5.6 as originally specified** — purchase requests, purchase orders, and vendor quote comparison. Two real deviations, both documented: PurchaseRequestStatus dropped "delayed"/"cancelled" (those describe an *order*, not a request — see PurchaseOrderStatus); "Delayed" is a **computed** dashboard stat (in-transit past `expected_delivery`), never a stored status. See [`docs/superpowers/specs/2026-08-29-procurement-service-design.md`](docs/superpowers/specs/2026-08-29-procurement-service-design.md). |
| AI Engine | 8007 | **Built** — rules-based extraction, no LLM (see `docs/invoice-ocr-plan.md`). Image uploads now go through a Document Preprocessing stage first (OpenCV boundary detection, background-removing smart crop, multi-document split) — `docs/invoice-ocr-plan.md §7` |
| PaddleOCR | 8008 (internal) | **Built** — OCR model server; not in the original blueprint, added by the LiteParse+PaddleOCR migration |
| Slack Connector | 8010 | **Built** |
| Email Connector | 8011 | **Built** |
| Documents Service | 8013 | **Built** — browser-upload document library; not in the original blueprint, see [`docs/documents-and-vendors-plan.md`](docs/documents-and-vendors-plan.md) §2 |
| Settings Service | 8009 | **Built** — §5.10's Company profile, Tax configuration and AI Automation toggles, each a lazily-created, company-scoped singleton (no signup-time provisioning hook from Auth Service). The five automation flags are real and persisted, but **not all gate real pipeline behaviour yet** — see [`docs/superpowers/specs/2026-08-29-settings-service-design.md`](docs/superpowers/specs/2026-08-29-settings-service-design.md) §3 for exactly which are enforced today. |
| Reports Service | 8014 (reassigned — §5.9's original 8008 now belongs to PaddleOCR) | **Built, generation made synchronous** — P&L, Cash Flow, Tax Summary, Sales, and Purchase reports computed live from Invoice/Transactions/Settings Service data on every request (no RabbitMQ/Celery worker: nothing here is OCR-slow). PDF (ReportLab, not WeasyPrint — no native GTK/Pango dependency) and Excel (openpyxl) are rendered on demand from a stored JSON payload, not pre-rendered files. **Balance Sheet is deliberately a simplified cash-position snapshot**, not a full balance sheet — this codebase tracks no fixed assets, receivables/payables, loans, or equity to report on. See [`docs/superpowers/specs/2026-08-29-reports-service-design.md`](docs/superpowers/specs/2026-08-29-reports-service-design.md). |

**Not built:** WhatsApp Connector (8012). **RAG-based AI Chat/Insights** (see the correction box
below — the current AI Assistant page is a scripted demo, not a live model call).

**RabbitMQ is still not built, and this document's Docker Compose example (§16) and Tech Stack
table (§14) are both aspirational, not current.** Every internal hand-off in this codebase is a
plain HTTP call — the Slack connector's `scanner_bridge.py` and Invoice Service's
`ai_engine_client.py` both hand off over HTTP and degrade to a clear error when the upstream
service is down, rather than assuming a queue exists to publish to. Any new connector (WhatsApp)
should integrate the same way; when RabbitMQ is eventually built, every hand-off upgrades together
rather than one at a time.

**Celery *is* real — but only for the two document connectors' background sync**, not for OCR or
report generation as originally planned. `slack-connector-worker` and `email-connector-worker`
(see the real `backend/infra/docker-compose.yml`) run `celery -A app.worker.celery_app worker`
against Redis as the broker, polling Slack/Gmail on a schedule and downloading new attachments.
Invoice OCR and PDF/Excel report generation are both synchronous HTTP calls — no queue, no
background worker — because neither turned out to need one at the load this project runs at.

**No OpenAI/Anthropic call exists anywhere in this codebase — not for OCR (by design, see §5.8's
correction) and not for the AI Assistant chat page either.** `/app/assistant`'s "Ask AI" chat is
today a **frontend-only scripted demo**: a fixed lookup table of canned answers to a handful of
sample questions (`frontend/src/routes/app.assistant.tsx`'s own `canned` object), with a generic
fallback for anything else typed. There is no chat endpoint, no LLM call, no RAG pipeline, and no
AI Engine route backing it. This is intentionally called out as **Planned, not built** — see the
new companion document `docs/finpilot-ai-description.md` for the actual RAG design under
consideration and why it hasn't been built yet.

Connector build status specifically lives in §5.11's table below, not here — check that table (or
`docs/selective-sync-plan.md` / `docs/email-connector-plan.md`) for what's current. The current
phase's own plan is [`docs/documents-and-vendors-plan.md`](docs/documents-and-vendors-plan.md).

---

## 1. What FinPilot AI Actually Is

**Product:** FinPilot AI — AI-Powered Accounting Automation Platform
**Target Users:** Pakistani SMEs (Small & Medium Enterprises)
**Currency:** PKR (Pakistani Rupees)

The app helps Pakistani business owners:
- **Scan invoices** using AI (extract data from PDFs, images, WhatsApp screenshots)
- **Generate sales invoices** (create and manage sales documents)
- **Track revenue** — sales, customers, payment methods
- **Track expenses** — with categories (Salaries, Fuel, Raw Material, etc.)
- **Manage employees** — payroll, salaries, bonuses, deductions
- **Handle procurement** — purchase requests, purchase orders, vendor comparison, approvals
- **Manage vendors** — ratings, spend tracking, payment terms
- **Get AI insights** — chat interface ("Which vendor costs us the most?")
- **Generate reports** — Profit & Loss, Balance Sheet, Cash Flow, Tax Summary, Sales, Purchase
- **View analytics** — KPI cards, revenue charts, forecasting

---

## 2. All Pages in the Frontend (That Your Backend Must Power)

Here are all the pages the frontend has, mapped to what API data each one needs:

| Frontend Route | Page | Backend Will Provide |
|---|---|---|
| `/` | Landing Page | Static — no backend needed |
| `/app` | Dashboard | KPIs, charts, recent invoices, upcoming payments, notifications |
| `/app/scanner` | AI Invoice Scanner | File upload endpoint, AI OCR, extracted invoice data |
| `/app/documents` | Documents | Per-connector document lists (Slack live; Email + WhatsApp planned), inline byte streaming for preview |
| `/app/invoices` | Invoice Generator | Create/list/manage sales invoices |
| `/app/records` | Saved Records | Validated/sent-to-accounting purchase invoices as a categorized cashbook — category-grouped list with per-category totals, per-record document preview, per-category PDF report |
| `/app/revenue` | Revenue Manager | Sales records, revenue by source |
| `/app/expenses` | Expenses | Expense records, categories, approval status |
| `/app/employees` | Employees | Employee profiles, salary data |
| `/app/procurement` | Procurement | Purchase requests, purchase orders, vendor comparison |
| `/app/vendors` | Vendors | Vendor profiles, spend, ratings |
| `/app/analytics` | Analytics | All chart data, forecast, profit margin, top customers |
| `/app/assistant` | AI Assistant | Chat endpoint — financial Q&A |
| `/app/reports` | Reports | Report generation, PDF/Excel download |
| `/app/settings` | Settings | Company profile, AI automation config |

---

## 3. The Architecture — Big Picture

```
                     ┌──────────────────────────────┐
                     │   FRONTEND (Already Exists)   │
                     │ React + TypeScript + Vite      │
                     │ TanStack Router + TanStack Query│
                     │ shadcn/ui + Tailwind CSS v4    │
                     │ Deployed: static host          │
                     └──────────────┬───────────────┘
                                    │ HTTPS / REST JSON
                                    ▼
                     ┌──────────────────────────────┐
                     │         API GATEWAY           │
                     │    (FastAPI — Port 8000)       │
                     │  ▸ Routes all requests         │
                     │  ▸ Verifies JWT tokens         │
                     │  ▸ Rate limiting (Redis)       │
                     │  ▸ Request logging + tracing   │
                     └───────────────┬───────────────┘
                                     │
    ┌─────────────┬──────────────────┼──────────────────┬──────────────┐
    ▼             ▼                  ▼                  ▼              ▼
┌────────┐  ┌─────────┐   ┌──────────────┐   ┌──────────────┐  ┌──────────┐
│  Auth  │  │Invoice  │   │  Transactions │   │   HR Service │  │Procurement│
│Service │  │Service  │   │   Service     │   │  (Port 8004) │  │ Service  │
│(8001)  │  │(8002)   │   │   (Port 8003) │   │  Employees   │  │ (8005)  │
│        │  │         │   │  Revenue +    │   │  Payroll     │  │ Purchase │
│ Login  │  │AI OCR   │   │  Expenses     │   │  Salaries    │  │ Requests │
│ Signup │  │Generate │   │  Customers    │   │              │  │ PO Mgmt  │
│ JWT    │  │ Manage  │   │               │   │              │  │ Approvals│
└────────┘  └─────────┘   └──────────────┘   └──────────────┘  └──────────┘

    ┌──────────────┬──────────────────┬──────────────────┐
    ▼              ▼                  ▼                  ▼
┌─────────┐  ┌──────────┐  ┌──────────────────┐  ┌──────────┐
│ Vendors │  │AI Engine │  │  Reports Service  │  │ Settings │
│ Service │  │ Service  │  │   (Port 8008)     │  │ Service  │
│ (8006)  │  │ (8007)   │  │  P&L, Balance     │  │ (8009)  │
│         │  │          │  │  Sheet, Cash Flow │  │          │
│ Vendor  │  │ AI Chat  │  │  Tax Summary      │  │ Company  │
│ Profiles│  │ Insights │  │  PDF/Excel Export │  │ Profile  │
│ Ratings │  │ Forecast │  │                   │  │ Config   │
└─────────┘  └──────────┘  └──────────────────┘  └──────────┘

                    ┌──────────────────────────┐
                    │       Message Broker      │
                    │        (RabbitMQ)         │
                    │  file_uploaded → AI OCR  │
                    │  ocr_done → save invoice │
                    │  report_requested → gen  │
                    └──────────────────────────┘

                    ┌──────────────────────────┐
                    │     Redis Cache           │
                    │  Rate limiting            │
                    │  AI response cache        │
                    │  Session store            │
                    └──────────────────────────┘
```

---

## 4. The Three Golden Rules

**Rule 1 — Each service has its own database.**
No service reads another service's database directly. If Invoice Service needs employee info, it calls HR Service's API. Never a shared database.

**Rule 2 — The Gateway is the only public door.**
The frontend NEVER calls individual services directly. Everything goes: Frontend → Gateway → Service.

**Rule 3 — Use the queue for slow work.**
AI invoice scanning, report PDF generation — these take time. Don't make the user wait. Use RabbitMQ: frontend gets "processing" response immediately, result is ready later.

---

## 5. Services — What Each One Does

### 5.1 API Gateway (Port 8000)
The front door. Every request from the frontend lands here.
- Verifies JWT token — if invalid, returns 401 immediately
- Injects `X-User-ID` and `X-Company-ID` headers for downstream services
- Routes to the right service based on URL path
- Rate limits per IP and per user (via Redis)
- Logs every request with a `trace_id` for debugging

### 5.2 Auth Service (Port 8001)
Handles who you are.
- `POST /auth/signup` — register new company + admin user
- `POST /auth/login` — returns JWT access token + refresh token
- `POST /auth/refresh` — get new access token using refresh token
- `POST /auth/logout` — invalidate refresh token
- Password hashing with Argon2
- **Database:** stores credentials and refresh tokens only

### 5.3 Invoice Service (Port 8002)
The core of the platform — handles all invoice operations.

**AI Invoice Scanner:**
- `POST /invoices/scan` — accepts file upload (PDF, image, WhatsApp screenshot up to 20MB)
- Sends file to AI Engine via RabbitMQ queue
- Returns `{ job_id, status: "processing" }` immediately
- `GET /invoices/scan/{job_id}` — poll for extracted results

**Invoice CRUD:**
- `GET /invoices` — list all scanned/purchase invoices with status filter (Processed, Pending, Duplicate, Needs Review)
- `POST /invoices` — manually create invoice
- `PUT /invoices/{id}` — update invoice fields (editable after AI extraction)
- `POST /invoices/{id}/validate` — mark as validated
- `POST /invoices/{id}/send-to-accounting` — finalize and book

**Invoice Generator (Sales Invoices):**
- `POST /invoices/sales` — generate a sales invoice from items
- `GET /invoices/sales` — list all sales invoices
- `GET /invoices/sales/{id}/pdf` — download as PDF

**Data models this service owns:**
```
Invoice: id, company_id, type (purchase/sale), vendor_id or customer_id,
         invoice_number, date, ntn, items[], subtotal, tax_rate, tax_amount,
         total, status, confidence_score, created_at
InvoiceItem: id, invoice_id, description, qty, rate, amount
```

### 5.4 Transactions Service (Port 8003)
Handles all money flowing in (Revenue) and out (Expenses).

**Revenue:**
- `GET /revenue` — list all revenue entries
- `POST /revenue` — create revenue entry from sales invoice
- `GET /revenue/sources` — revenue by source (Retail, Wholesale, Online, Exports, Services)
- `GET /revenue/monthly` — monthly revenue data for charts

**Expenses:**
- `GET /expenses` — list all expenses with filters (category, status, date range)
- `POST /expenses` — create expense entry
- `PUT /expenses/{id}/approve` — approve expense
- `PUT /expenses/{id}/reject` — reject expense
- `GET /expenses/categories` — expense breakdown by category

**Dashboard KPIs:**
- `GET /transactions/kpis` — Today's Revenue, Monthly Revenue, Monthly Expenses, Net Profit, Cash Balance, all in PKR

**Data models:**
```
Revenue: id, company_id, customer_id, invoice_id, date, amount_pkr,
         source (Retail/Wholesale/Online/Exports/Services), payment_method, status

Expense: id, company_id, category (Salaries/Raw Material/Fuel & Transport/Utilities/Rent/Marketing/Office),
         vendor_id, date, amount_pkr, payment_method (Bank Transfer/Online/Cheque/Cash/Card),
         status (Approved/Pending/Rejected), reference_id
```

### 5.5 HR Service (Port 8004)
Manages employees and payroll. Specific to Pakistani SME context — handles local payroll logic.

- `GET /employees` — list all employees with salary info
- `POST /employees` — add new employee
- `PUT /employees/{id}` — update employee details
- `GET /employees/payroll` — current month payroll summary
- `POST /employees/payroll/process` — trigger salary payment for all
- `GET /employees/payroll/history` — past payroll records

**Data models:**
```
Employee: id, company_id, name, department, avatar_initials,
          salary_pkr, bonus_pkr, deductions_pkr, net_salary_pkr,
          payment_status (Paid/Processing/Pending), joining_date, active

Department: Finance, Operations, Sales, Procurement, Accounts, IT, HR, Logistics
```

### 5.6 Procurement Service (Port 8005)
Full procurement lifecycle — from request to delivered order.

**Purchase Requests:**
- `GET /procurement/requests` — list purchase requests
- `POST /procurement/requests` — create new purchase request
- `POST /procurement/requests/{id}/approve` — approve request
- `GET /procurement/requests/{id}/timeline` — approval timeline steps

**Purchase Orders:**
- `GET /procurement/orders` — list all purchase orders
- `POST /procurement/orders` — create PO from approved request
- `PUT /procurement/orders/{id}/status` — update delivery status

**Vendor Comparison:**
- `GET /procurement/vendor-comparison` — comparison table for active bids

**Dashboard Stats:**
- `GET /procurement/stats` — Pending, Completed, Delayed, Cancelled counts

**Data models:**
```
PurchaseRequest: id, company_id, item_description, department, requester_employee_id,
                 amount_pkr, status (Pending Approval/Approved/Delayed/Cancelled)

PurchaseOrder: id, company_id, vendor_id, pr_id, date, amount_pkr,
               expected_delivery, status (In Transit/Delivered/Cancelled)

ProcurementTimeline: request_id, steps[{title, detail, date, completed}]
```

### 5.7 Vendors Service (Port 8006)
Manage all supplier/vendor relationships.

- `GET /vendors` — list all vendors
- `POST /vendors` — add new vendor
- `PUT /vendors/{id}` — update vendor details
- `GET /vendors/{id}/invoices` — all invoices from this vendor
- `GET /vendors/top` — top vendors by spend (for charts)

**Data models:**
```
Vendor: id, company_id, name, category (Raw Material/Steel/Packaging/Fuel/Transport/Office),
        city, total_spend_pkr, rating (0-5), payment_terms (Net 30/Net 45/Advance/etc.),
        status (Active/Inactive/Review), ntn
```

### 5.8 AI Engine Service (Port 8007)
The brain of FinPilot AI. Three things:

**1. Invoice OCR (consumes from queue):**
> **Corrected — see `docs/invoice-ocr-plan.md`.** The mechanism below (upload the raw file straight
> to a vision-capable LLM and trust its self-reported confidence) has been replaced twice, not once:
> first with a two-stage pipeline that extracted text locally (PyMuPDF/Tesseract) and sent that text
> to a text-only LLM for structuring, and then — per `docs/invoice-ocr-plan.md` §2a and
> `docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md` — that LLM structuring step was itself
> replaced with a **fully rules-based structuring engine (no AI/LLM call anywhere in this pipeline)**:
> label-anchored field extraction, geometric line-item table reconstruction, and arithmetic
> cross-validation. The confidence score is a composite of OCR confidence, extraction-method
> confidence, and arithmetic consistency — no self-reported model confidence at all. Read both linked
> docs before implementing this section — the rules-report doc in particular states plainly that a
> rules-only system has a real, researched ~70-80% clean-extraction ceiling on line items specifically,
> which is *why* the confidence-gated "Needs Review" routing below is treated as core to this design,
> not a fallback for when the rules "fail."
- Receives file from Invoice Service via RabbitMQ
- Extracts text + positioned words (PyMuPDF for PDFs with a real text layer, Tesseract OCR for
  scanned PDFs/images — see `docs/invoice-ocr-plan.md` §2 for the routing logic)
- Runs the rules-based structuring engine (`backend/libs/invoice_extraction/`) over the extracted
  text/positions — label-anchored extraction for header fields, geometric table reconstruction for
  line items, arithmetic validation throughout
- Extracts: vendor name, invoice number, date, NTN, line items, tax rate, totals
- Returns a composite confidence score (OCR confidence + extraction-method confidence + arithmetic
  consistency — see `docs/invoice-ocr-plan.md` §3) that routes the result to auto-process /
  needs-review / needs-review-high-priority
- Publishes result back to queue

**2. AI Chat Assistant:**
- `POST /ai/chat` — send a message, get financial answer
- Has access to company's actual data (calls other services for context)
- Example questions it handles: "Why did expenses increase?", "Which vendor costs most?", "Show unpaid invoices", "Estimated profit?"
- Responses are grounded in real PKR numbers from the company's data

**3. AI Insights Generation:**
- `GET /ai/insights` — return current AI insight cards
- Runs periodically or on-demand to generate:
  - Revenue trend insights
  - Expense anomaly alerts (e.g., "Fuel spending increased 22%")
  - Vendor volume discount opportunities
  - Cash flow health summary

**Data models:**
```
AIJob: id, company_id, type (ocr/chat/insight), status (queued/processing/done/failed),
       input_ref, output_json, confidence_score, created_at

ChatMessage: id, session_id, company_id, role (user/ai), content, timestamp

AIInsight: id, company_id, title, body, tone (positive/neutral/warning),
           generated_at, active
```

### 5.9 Reports Service (Port 8008)
Generates financial reports. This is a complex service because it pulls data from multiple other services.

- `POST /reports/profit-loss` — generate P&L for a period
- `POST /reports/balance-sheet` — generate Balance Sheet as of a date
- `POST /reports/cash-flow` — generate Cash Flow statement
- `POST /reports/tax-summary` — generate FBR-ready tax schedule
- `POST /reports/sales` — generate Sales Report by customer/channel
- `POST /reports/purchases` — generate Purchase Report by vendor
- `GET /reports/{id}/pdf` — download completed report as PDF
- `GET /reports/{id}/excel` — download completed report as Excel (.xlsx)

Report generation is async — triggered via RabbitMQ worker (Celery), PDF rendered with WeasyPrint or ReportLab.

**Data models:**
```
Report: id, company_id, type, period_start, period_end, status (queued/generating/ready/failed),
        file_path_pdf, file_path_excel, generated_at
```

### 5.10 Settings Service (Port 8009)
Company profile and configuration.

- `GET /settings/company` — get company profile
- `PUT /settings/company` — update company name, NTN, address, logo
- `GET /settings/automation` — get AI automation toggles
- `PUT /settings/automation` — enable/disable AI features
- `GET /settings/tax` — tax rate configuration (default 18% GST for Pakistan)
- `PUT /settings/tax` — update tax rates

**Data models:**
```
Company: id, name, ntn, address, city, logo_url, industry, phone, email
AutomationSettings: auto_categorize_expenses, auto_detect_duplicates,
                    auto_insights, smart_vendor_suggestions, enabled_ai
TaxSettings: default_gst_rate (18%), withholding_tax_rate, filer_status
```

---

### 5.11 Document Connector Services (Ports 8010+)

A family of services that pull financial documents out of the tools a business
already uses, so invoices and receipts reach FinPilot without anyone uploading
them by hand. Each connector is a separate service with its own database,
following the same database-per-service rule as everything above.

| Connector | Port | Status |
|---|---|---|
| Slack | 8010 | **Built** — OAuth, discovery, categorisation, S3 storage, Celery sync, sync-scope picker |
| Email | 8011 | **In progress** — Gmail OAuth foundation (current phase). See [`docs/email-connector-plan.md`](docs/email-connector-plan.md) |
| WhatsApp | 8012 | Planned |

**Why email matters most of the three:** most Pakistani SME invoices arrive as email
attachments — a vendor emails a PDF, an airline emails a receipt — and today someone
downloads it and re-uploads it into the scanner by hand. Connecting a mailbox once removes
that step. Built to the identical contract below, so it slots into the same frontend
aggregation Slack already uses rather than becoming a special case.

**Shared contract.** Every connector exposes the same shape under
`/api/v1/<source>/*`, which is what lets the frontend treat them uniformly:

- `GET  /connect` → 302 to the provider's OAuth consent screen
- `GET  /callback` → completes OAuth, stores an encrypted token
- `GET  /status` → `{ connected, account_name, scopes, installed_at }`
- `DELETE /installation` → revoke and forget the connection
- `POST /sync/` → queue a background sync; `GET /sync/{id}` for progress
- `GET  /files/` → paginated documents, filterable by category
- `GET  /files/{id}/content` → stream the stored bytes, `Content-Disposition: inline`
- `PATCH /files/{id}/category` → human correction, recorded as `manual_override`
- `POST /files/{id}/send-to-scanner` → hand the document to the Invoice Service

**Why `/content` streams through the service** rather than handing out a signed
S3 URL: it keeps every byte behind the company-scoping check, and no storage URL
is ever exposed to the browser. The `inline` disposition is what lets the
Documents page preview a PDF in the browser's own viewer.

**Frontend aggregation.** `frontend/src/lib/documents.ts` maps each connector
into one `UnifiedDocument` shape and registers it. `/app/documents` renders one
column per registered source. A connector that does not exist yet is registered
as `planned` so its column explains what it will collect — the page never invents
documents for a source that isn't built.

#### Sync scope (planned)

Every connector pulls from many *conversations* — Slack channels and DMs, and
later email folders and WhatsApp threads. Syncing all of them by default does not
survive contact with a real workspace: a three-year-old company Slack holds
thousands of files, mostly screenshots and design work rather than invoices, and
pulling every private DM attachment into a finance system is a privacy problem
before it is a storage one.

Connectors therefore gain a **saved sync scope** — which conversations are in,
how far back to look, and whether DMs are included (off by default). Planned
endpoints, same shape for every connector:

```
GET  /api/v1/<source>/conversations        list with file counts, cheap metadata only
PUT  /api/v1/<source>/conversations/scope  save which are in scope + date window
GET  /api/v1/<source>/files/?conversation_id=…&group_by=conversation
```

Conversations also carry their own status and last error, so a channel the bot
was never invited to appears in the UI as a fixable problem rather than as an
unexplained drop in the processed count.

Full design, rationale and phasing: [`docs/selective-sync-plan.md`](docs/selective-sync-plan.md).

### 5.12 Human-in-the-Loop Document Verification & Needs Review Architecture

To ensure zero unverified documents enter the financial ledger and Cash Book, all purchase invoices, receipts, and bills newly ingested through any channel (Mobile Camera Capture, Email Connector, Slack Connector, or manual Scanner Upload) undergo a strict two-stage human-in-the-loop review pipeline before posting:

1. **Ingestion & Pending State (`status: needs_review | processed`)**:
   - Ingested files are stored in S3/MinIO and processed by AI Engine for OCR extraction, vendor candidate selection, date, and totals computation.
   - Initial status is marked `needs_review` or `processed` (`transaction_status: transactional`). They are deliberately **excluded** from the validated Cash Book query (`GET /invoices/?status=validated,sent_to_accounting`) until approved.

2. **Global Header "Needs Review" Notification Center (`NeedsReviewCenter`)**:
   - Integrated directly into the top application header (`app-shell.tsx`).
   - Displays a dynamic real-time badge with the count of pending documents across all sources.
   - Clicking opens a rich popover notification center showing document cards with source origin (e.g., Mobile Camera, Email Sync, Slack Sync, Scanner Upload), thumbnail, vendor, and amount.
   - Clicking "Review Now" opens the interactive `InvoiceReviewDialog`.

3. **Interactive Document Review & Edit Modal (`InvoiceReviewDialog`)**:
   - Side-by-side inspection layout:
     - Left: Full rendered document preview (`InvoiceDocumentPreview`) defaulting to 50% centered scale.
     - Right: Direct editing of extracted fields (Vendor / Payee, Category, Payment Method [Cash / Online], Expense Date, Invoice Number, NTN, Total, Subtotal, Tax Amount).
   - "Submit & Validate" action commits corrections via `PUT /invoices/{id}` and approves the document via `POST /invoices/{id}/validate`.

4. **Saved Records Dedicated Verification Queue (`NeedsReviewSection`)**:
   - Located at the bottom of the Saved Records page (`app.records.tsx`) with a high-visibility, red-tinted alert style (`border-destructive/30 bg-destructive/5`).
   - Provides accountants with an unmissable overview of all unposted documents awaiting approval, complete with inline "Review & Submit" triggers.
   - Upon submission, records immediately migrate into the verified cashbook category tables above.

5. **50% Centered Default Zoom Preview (`InvoiceDocumentPreview`)**:
   - Default zoom level initialized to `0.5` (50% scale) with `transformOrigin: "center center"`.
   - Ensures tall receipts, mobile snapshots, and standard A4 documents are legible in full without clipping or immediate scroll thrash, while supporting smooth zoom from 25% up to 300%.

---

## 6. Full Monorepo Folder Structure

One GitHub repository, everything in one place. Easier to manage for a solo developer.

```
finpilot-ai-showcase/                   ← Root of the repo (already exists)
│
├── frontend/                           ← The existing React app (move src/ here)
│   ├── src/                            ← Everything currently in your repo
│   ├── package.json
│   ├── vite.config.ts
│   └── ...
│
├── backend/                            ← NEW: Everything you build lives here
│   │
│   ├── services/                       ← All 9 FastAPI microservices
│   │   ├── gateway/                    ← API Gateway (Port 8000)
│   │   ├── auth/                       ← Auth Service (Port 8001)
│   │   ├── invoice/                    ← Invoice Service (Port 8002)
│   │   ├── transactions/               ← Revenue + Expenses (Port 8003)
│   │   ├── hr/                         ← Employees + Payroll (Port 8004)
│   │   ├── procurement/                ← Purchase Requests + Orders (Port 8005)
│   │   ├── vendors/                    ← Vendor Management (Port 8006)
│   │   ├── ai-engine/                  ← AI OCR + Chat + Insights (Port 8007)
│   │   ├── reports/                    ← Report Generation (Port 8008)
│   │   └── settings/                   ← Company Config (Port 8009)
│   │
│   ├── libs/                           ← Shared Python package (used by all services)
│   │   └── shared/
│   │       ├── auth.py                 ← JWT verification logic
│   │       ├── logging.py              ← Structured JSON logging
│   │       ├── base_schemas.py         ← Standard error response format
│   │       ├── exceptions.py           ← Common exception types
│   │       └── events/
│   │           └── schemas.py          ← RabbitMQ event contract definitions
│   │
│   ├── infra/                          ← Infrastructure setup
│   │   ├── docker-compose.yml          ← Local dev (runs everything with one command)
│   │   ├── docker-compose.prod.yml     ← Production overrides
│   │   └── k8s/                        ← Kubernetes (for later when you scale)
│   │       ├── gateway/
│   │       ├── auth/
│   │       └── ...
│   │
│   ├── docs/                           ← Backend documentation
│   │   ├── api-contracts.md            ← All endpoint definitions
│   │   └── adr/                        ← Why you made each architecture decision
│   │       ├── 0001-database-per-service.md
│   │       └── 0002-async-ocr-queue.md
│   │
│   └── scripts/
│       ├── bootstrap-dev.sh            ← One script to set up local env
│       └── seed-data.py               ← Insert Pakistani SME test data
│
└── .github/
    └── workflows/
        ├── auth-ci.yml                 ← One CI pipeline per service
        ├── invoice-ci.yml
        └── ...
```

---

## 7. Inside Each Service — Folder Structure

Every service has the same internal shape. Learn it once, apply it everywhere.

Here's the **Invoice Service** as a full example (it's the most representative):

```
backend/services/invoice/
│
├── app/
│   │
│   ├── main.py                          ← FastAPI app startup, middleware, lifespan
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py                    ← Registers all route groups
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── scanner.py               ← POST /invoices/scan endpoints
│   │       ├── invoices.py              ← Invoice CRUD endpoints
│   │       └── sales.py                 ← Sales invoice generator endpoints
│   │
│   ├── schemas/                         ← What data comes IN and goes OUT (Pydantic)
│   │   ├── __init__.py
│   │   ├── invoice.py                   ← InvoiceCreate, InvoiceResponse, InvoiceUpdate
│   │   ├── scan.py                      ← ScanJobResponse, ExtractedInvoiceData
│   │   └── sales.py                     ← SalesInvoiceCreate, SalesInvoiceResponse
│   │
│   ├── services/                        ← Business logic — the "brain"
│   │   ├── __init__.py
│   │   ├── invoice_service.py           ← CRUD logic for invoices
│   │   ├── scan_service.py              ← Handles file validation, job creation
│   │   └── sales_service.py            ← Sales invoice generation logic
│   │
│   ├── repositories/                    ← Database access only — no business logic here
│   │   ├── __init__.py
│   │   └── invoice_repository.py        ← save_invoice(), get_by_company(), update_status()
│   │
│   ├── models/                          ← SQLAlchemy table definitions
│   │   ├── __init__.py
│   │   └── invoice.py                   ← Invoice, InvoiceItem table models
│   │
│   ├── events/                          ← RabbitMQ communication
│   │   ├── __init__.py
│   │   ├── publisher.py                 ← Publishes FileUploaded event to queue
│   │   └── consumer.py                  ← Listens for OCRCompleted event from AI Engine
│   │
│   ├── tasks/                           ← Celery background jobs (if needed)
│   │   └── __init__.py
│   │
│   └── core/
│       ├── __init__.py
│       ├── config.py                    ← All settings loaded from .env
│       ├── database.py                  ← PostgreSQL async connection setup
│       ├── storage.py                   ← File upload to S3/local storage
│       ├── security.py                  ← JWT verification (from shared lib)
│       └── exceptions.py               ← Invoice-specific error types
│
├── tests/
│   ├── conftest.py                      ← Test DB, fixtures
│   ├── unit/
│   │   └── test_scan_service.py         ← Test business logic
│   └── integration/
│       └── test_invoice_api.py          ← Test actual HTTP endpoints
│
├── alembic/                             ← Database migrations for THIS service only
│   ├── versions/
│   └── env.py
│
├── Dockerfile
├── pyproject.toml                       ← Dependencies for this service only
├── .env.example                         ← Shows required env vars, no real values
└── README.md                            ← How to run this service
```

### Simpler Service Example — HR Service

Not every service needs all that. HR Service is simpler:

```
backend/services/hr/
├── app/
│   ├── main.py
│   ├── api/
│   │   └── v1/
│   │       ├── employees.py            ← /employees endpoints
│   │       └── payroll.py              ← /employees/payroll endpoints
│   ├── schemas.py                      ← EmployeeCreate, EmployeeResponse, PayrollSummary
│   ├── models.py                       ← Employee, PayrollRecord DB models
│   ├── repository.py                  ← DB read/write for employees
│   ├── service.py                      ← Payroll calculation logic (salary - deductions + bonus)
│   └── core/
│       ├── config.py
│       └── database.py
├── tests/
├── alembic/
├── Dockerfile
└── pyproject.toml
```

**Rule:** Start flat. Add more layers only when the service grows complex enough to need them.

---

## 8. What Each Folder Does (Plain English)

| Folder | What it is | Think of it as |
|---|---|---|
| `api/v1/` | Handles HTTP — takes request, returns response | The receptionist |
| `schemas/` | Defines shape of data in and out (Pydantic) | The form template |
| `services/` | Business rules and decisions | The brain |
| `repositories/` | Reads and writes to database | The filing cabinet |
| `models/` | Database table structure | The database blueprint |
| `events/` | RabbitMQ send/receive | The post office |
| `tasks/` | Long background jobs (Celery) | The background worker |
| `core/` | Config, DB connection, security setup | The engine room |

---

## 9. The Shared Library

Small Python package installed by all services. Only infrastructure code — never domain logic.

```
backend/libs/shared/
├── shared/
│   ├── __init__.py
│   ├── auth.py               ← JWT verification (one implementation, used everywhere)
│   ├── logging.py            ← Structured JSON logging setup
│   ├── base_schemas.py       ← Standard error response:
│   │                             { "error": "Not Found", "detail": "...", "code": 404, "trace_id": "..." }
│   ├── exceptions.py         ← Base exception classes
│   └── events/
│       ├── __init__.py
│       └── schemas.py        ← RabbitMQ event shapes:
│                                 FileUploaded, OCRCompleted, ReportRequested, ReportReady
└── pyproject.toml
```

**What to put here:** JWT verification, logging setup, standard error format, event contract definitions.
**Never put here:** Invoice model, Employee model, anything business-specific. Keep business objects inside the service that owns them.

---

## 10. How Frontend & Backend Connect

Currently `data.ts` has everything hardcoded. When you build the backend, the frontend replaces it with API calls using TanStack Query. Here's the mapping:

### From Dummy Data → Real API Endpoints

| `data.ts` export | Replaced by | API Endpoint |
|---|---|---|
| `kpis[]` | Transactions Service | `GET /api/v1/transactions/kpis` |
| `revenueVsExpenses[]` | Transactions Service | `GET /api/v1/transactions/chart/revenue-vs-expenses` |
| `expenseCategories[]` | Transactions Service | `GET /api/v1/expenses/categories` |
| `revenueSources[]` | Transactions Service | `GET /api/v1/revenue/sources` |
| `cashFlow[]` | Transactions Service | `GET /api/v1/transactions/chart/cash-flow` |
| `topVendors[]` | Vendors Service | `GET /api/v1/vendors/top` |
| `invoiceStatus[]` | Invoice Service | `GET /api/v1/invoices/status-summary` |
| `recentInvoices[]` | Invoice Service | `GET /api/v1/invoices?limit=7` |
| `employees[]` | HR Service | `GET /api/v1/employees` |
| `vendors[]` | Vendors Service | `GET /api/v1/vendors` |
| `expenses[]` | Transactions Service | `GET /api/v1/expenses` |
| `purchaseRequests[]` | Procurement Service | `GET /api/v1/procurement/requests` |
| `purchaseOrders[]` | Procurement Service | `GET /api/v1/procurement/orders` |
| `vendorComparison[]` | Procurement Service | `GET /api/v1/procurement/vendor-comparison` |
| `insights[]` | AI Engine Service | `GET /api/v1/ai/insights` |
| `upcomingPayments[]` | Transactions Service | `GET /api/v1/transactions/upcoming-payments` |
| `notifications[]` | Gateway / Events | `GET /api/v1/notifications` |
| `topCustomers[]` | Transactions Service | `GET /api/v1/revenue/top-customers` |
| `forecast[]` | AI Engine Service | `GET /api/v1/ai/forecast` |
| `profitMargin[]` | Transactions Service | `GET /api/v1/transactions/chart/profit-margin` |
| `extractedInvoice` | Invoice Service (AI OCR) | `GET /api/v1/invoices/scan/{job_id}` |
| `extractedSale` | Invoice Service (AI OCR) | `GET /api/v1/invoices/scan/{job_id}` |

All amounts are in **PKR** (Pakistani Rupees). The `money()` formatter in the frontend adds "PKR " prefix — the backend returns plain numbers.

---

## 11. The AI Invoice Scanner Flow (Step by Step)

This is the most important feature. Here's exactly how it works end-to-end:

```
1. User drags a file (PDF/image/WhatsApp screenshot) to the scanner page
   ↓
2. Frontend: POST /api/v1/invoices/scan
   Content-Type: multipart/form-data
   Body: { file: <binary>, type: "purchase" }
   ↓
3. Invoice Service:
   - Validates file type and size (max 20MB)
   - Saves file to object storage (S3 or MinIO)
   - Creates AIJob record { status: "queued" }
   - Publishes to RabbitMQ: FileUploaded { job_id, file_path, company_id }
   - Returns immediately: { job_id: "abc-123", status: "processing" }
   ↓
4. Frontend shows progress spinner (polling GET /api/v1/invoices/scan/abc-123)
   ↓
5. AI Engine Service (listening to RabbitMQ):
   - Picks up FileUploaded event
   - Reads file from storage
   - Extracts plain text + positioned words first (PyMuPDF for a real PDF text layer; PyMuPDF
     page-render → Tesseract OCR for scanned PDFs/images — see `docs/invoice-ocr-plan.md` §2, which
     replaces the "call OpenAI/Anthropic with the raw file" step this used to describe)
   - Runs the rules-based structuring engine (no AI call — see `docs/invoice-ocr-plan.md` §2a and
     `docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md`):
     label-anchored extraction for vendor_name, invoice_number, date, ntn, tax_rate, totals;
     geometric table reconstruction for line_items (description, qty, rate, amount)
   - Runs arithmetic cross-validation (line-item and document-total level) against the extracted values
   - Calculates a composite confidence score (OCR confidence + extraction-method confidence +
     arithmetic consistency — `docs/invoice-ocr-plan.md` §3), not a self-reported model number
   - Saves extracted data to AIJob, tagged for auto-process / needs-review / needs-review-high-priority
     depending on the composite confidence
   - Publishes: OCRCompleted { job_id, extracted_data, confidence_score }
   ↓
6. Invoice Service picks up OCRCompleted:
   - Updates AIJob status to "done"
   - Creates Invoice record from extracted data
   ↓
7. Frontend polls and gets result:
   GET /api/v1/invoices/scan/abc-123
   Response: {
     "status": "done",
     "confidence": 0.96,
     "data": {
       "vendor": "ABC Traders",
       "number": "INV-2026-1841",
       "date": "2026-08-04",
       "ntn": "3947261-8",
       "items": [
         { "desc": "Steel Sheet 4mm", "qty": 12, "rate": 9800 },
         { "desc": "Welding Rods (box)", "qty": 6, "rate": 3200 },
         { "desc": "Transport Charges", "qty": 1, "rate": 8500 }
       ],
       "tax_rate": 18,
       "subtotal": 155300,
       "tax": 27954,
       "total": 183254
     }
   }
   ↓
8. Frontend shows editable fields pre-filled with extracted data
   User can edit if needed, then clicks Validate → Save → Send to Accounting
```

---

## 12. The AI Assistant Flow

The chat page at `/app/assistant` — "Ask AI about your business":

```
1. User types: "Which vendor costs us the most?"
   ↓
2. Frontend: POST /api/v1/ai/chat
   Body: { message: "Which vendor costs us the most?", session_id: "xyz" }
   ↓
3. AI Engine Service:
   - Classifies intent → "vendor_spend_query"
   - Calls Vendors Service: GET /vendors/top (gets real PKR spend data)
   - Calls Invoice Service: GET /invoices?vendor_id=abc (gets invoice count)
   - Builds context string with real data
   - Calls OpenAI/Anthropic with:
     System: "You are FinPilot AI, accounting copilot for Pakistani SMEs.
              Answer in plain English using the company data provided."
     Context: "Vendor spend data: ABC Traders PKR 1,240,000 (28 invoices)..."
     User: "Which vendor costs us the most?"
   - Returns grounded answer with real PKR figures
   ↓
4. Response to frontend:
   "ABC Traders — PKR 1.24M across 28 invoices this quarter
    (30% of total vendor spend). At this volume you qualify for a
    4% bulk discount, worth roughly PKR 49,600 per quarter."
```

---

## 13. Service Communication Rules

### Use REST (sync HTTP) when:
- User is waiting for the response on screen
- You need data from another service to complete the current request
- Example: Dashboard loads → Gateway calls Transactions Service for KPIs

### Use RabbitMQ (async events) when:
- Work takes more than 2-3 seconds
- User doesn't need to wait for the result
- Example: Invoice file uploaded → AI processing → result available later

| Scenario | Pattern |
|---|---|
| Login, get token | REST (sync) |
| Load dashboard KPIs | REST (sync) |
| Upload invoice for AI scan | Async (RabbitMQ) |
| Generate P&L report PDF | Async (RabbitMQ + Celery) |
| AI chat response | REST (sync, but with longer timeout) |
| Send notification | Async (RabbitMQ, fire and forget) |

---

## 14. Full Tech Stack

> **This table mixes original plan and current reality — read the Status column.** For the
> as-built stack with real package/framework versions in one place, see the new companion
> document [`docs/finpilot-ai-description.md`](finpilot-ai-description.md) §"Technology Stack" —
> that document is kept in sync with the actual `pyproject.toml`/`package.json` files, this
> table is not.

| Area | Technology | Status | Why This Choice |
|---|---|---|---|
| **Language** | Python 3.11–3.13 | **As planned** | AI/ML ecosystem, FastAPI, excellent libraries |
| **Framework** | FastAPI | **As planned** | Async, auto OpenAPI docs, type-safe, fast |
| **Server** | Uvicorn | **As planned** (no Gunicorn layer added) | Async ASGI server |
| **Database** | PostgreSQL 16 (per service) | **As planned** | Reliable, relational, perfect for financial data |
| **ORM** | SQLAlchemy 2.0 (async) | **As planned** | Industry standard, async support |
| **Migrations** | Alembic | **As planned** | Tracks DB schema changes, works with SQLAlchemy |
| **Validation** | Pydantic v2 | **As planned** | Built into FastAPI, very fast, type-safe |
| **Cache** | Redis | **As planned** | Rate limiting, Celery broker for the two connectors |
| **Queue** | ~~RabbitMQ~~ | **Not built** | Every hand-off is plain HTTP instead — see the correction box near the top of this document |
| **Background Jobs** | Celery + Redis | **Built, narrower than planned** | Real, but only for Slack/Email connector background sync — OCR and report generation are synchronous HTTP, not queued |
| **Auth** | JWT + Argon2 | **As planned** | JWT for stateless auth, Argon2 for password hashing |
| **OCR** | LiteParse + PaddleOCR (primary), PyMuPDF + Tesseract (fallback/legacy path) | **Built, evolved past the original plan** | Deterministic, local, no LLM — see `docs/invoice-ocr-plan.md`. PaddleOCR/LiteParse were adopted mid-project for materially better real-world accuracy on messy photos; PyMuPDF+Tesseract kept as an explicit rollback path |
| **Invoice structuring** | Rules engine (`backend/libs/invoice_extraction/`) — label anchors, geometric table reconstruction, arithmetic validation | **Built, as planned** | No AI/LLM call — see `docs/invoice-ocr-plan.md` §2a and `docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md` |
| **AI (chat/insights)** | ~~OpenAI GPT-4o or Anthropic Claude~~ | **Not built** | The AI Assistant page is a scripted frontend demo (fixed canned answers) today — no LLM call anywhere in the codebase. See the correction box near the top of this document and `docs/finpilot-ai-description.md` for the real RAG plan |
| **File Storage** | MinIO (local dev) / S3-compatible in prod | **As planned** | Invoice file storage (PDFs, images) |
| **PDF Generation** | ReportLab (not WeasyPrint) | **Built, one substitution** | Avoids WeasyPrint's native GTK/Pango system dependency |
| **Excel Export** | openpyxl | **As planned** | Generate .xlsx reports |
| **HTTP Client** | httpx (async) | **As planned** | Service-to-service REST calls |
| **Container** | Docker + Docker Compose | **As planned** | Local dev environment |
| **CI/CD** | GitHub Actions | **Not yet set up** | Planned, one pipeline per service |
| **Testing** | pytest + httpx + pytest-asyncio | **Built** | Unit + integration tests, real suites exist per service |
| **Tracing** | OpenTelemetry | **Not built** | Planned |
| **Logging** | Python `logging` (structured where added) | **Partially built** | `structlog` was the original plan; services currently use the standard library logger |

---

## 15. Environment Variables Per Service

Each service reads from its own `.env`. **Never commit real values — only `.env.example`.**

Example for AI Engine Service:

```bash
# backend/services/ai-engine/.env.example

# App
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8007

# This service's own database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@ai-engine-db:5432/ai_engine_db

# Cache
REDIS_URL=redis://redis:6379/0

# Queue
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/

# AI Providers (use one or both)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
AI_MODEL=gpt-4o                   # or claude-sonnet-4-6
AI_MAX_TOKENS=2000
AI_OCR_TIMEOUT_SECONDS=60

# Internal services this one calls (for chat context)
VENDORS_SERVICE_URL=http://vendors:8006
INVOICE_SERVICE_URL=http://invoice:8002
TRANSACTIONS_SERVICE_URL=http://transactions:8003

# JWT (same key as auth service — for token verification)
JWT_SECRET_KEY=your-long-random-secret-key
JWT_ALGORITHM=HS256
```

---

## 16. Docker Compose — Local Development

> **This section is the original illustrative example, not the real file.** It predates services
> added later (PaddleOCR, Documents Service, the Slack/Email connectors and their Celery workers),
> uses RabbitMQ (not actually deployed), and uses different service/port names than what's really
> running. For the real, current compose file, open
> [`backend/infra/docker-compose.yml`](../backend/infra/docker-compose.yml) directly — one command
> still runs the whole system, it's just a longer file than this example shows.

One command runs the whole system on your laptop.

```yaml
# backend/infra/docker-compose.yml

services:

  # ─── API GATEWAY ──────────────────────────────────────────────
  gateway:
    build: ./services/gateway
    ports: ["8000:8000"]           ← Only this port is exposed to your browser
    environment:
      AUTH_SERVICE_URL: http://auth:8001
      INVOICE_SERVICE_URL: http://invoice:8002
      TRANSACTIONS_SERVICE_URL: http://transactions:8003
      HR_SERVICE_URL: http://hr:8004
      PROCUREMENT_SERVICE_URL: http://procurement:8005
      VENDORS_SERVICE_URL: http://vendors:8006
      AI_ENGINE_SERVICE_URL: http://ai-engine:8007
      REPORTS_SERVICE_URL: http://reports:8008
      SETTINGS_SERVICE_URL: http://settings:8009
    depends_on: [auth, invoice, transactions, hr, procurement, vendors, ai-engine, reports]

  # ─── AUTH ──────────────────────────────────────────────────────
  auth:
    build: ./services/auth
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@auth-db:5432/auth_db
      REDIS_URL: redis://redis:6379/1
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    depends_on: [auth-db, redis]

  auth-db:
    image: postgres:16
    environment: { POSTGRES_DB: auth_db, POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres }
    volumes: [auth-data:/var/lib/postgresql/data]

  # ─── INVOICE ───────────────────────────────────────────────────
  invoice:
    build: ./services/invoice
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@invoice-db:5432/invoice_db
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      MINIO_URL: http://minio:9000
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    depends_on: [invoice-db, rabbitmq, minio]

  invoice-db:
    image: postgres:16
    environment: { POSTGRES_DB: invoice_db, POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres }
    volumes: [invoice-data:/var/lib/postgresql/data]

  # ─── TRANSACTIONS ──────────────────────────────────────────────
  transactions:
    build: ./services/transactions
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@transactions-db:5432/transactions_db
    depends_on: [transactions-db]

  transactions-db:
    image: postgres:16
    volumes: [transactions-data:/var/lib/postgresql/data]

  # ─── HR ────────────────────────────────────────────────────────
  hr:
    build: ./services/hr
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@hr-db:5432/hr_db
    depends_on: [hr-db]

  hr-db:
    image: postgres:16
    volumes: [hr-data:/var/lib/postgresql/data]

  # ─── PROCUREMENT ───────────────────────────────────────────────
  procurement:
    build: ./services/procurement
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@procurement-db:5432/procurement_db
    depends_on: [procurement-db]

  procurement-db:
    image: postgres:16
    volumes: [procurement-data:/var/lib/postgresql/data]

  # ─── VENDORS ───────────────────────────────────────────────────
  vendors:
    build: ./services/vendors
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@vendors-db:5432/vendors_db
    depends_on: [vendors-db]

  vendors-db:
    image: postgres:16
    volumes: [vendors-data:/var/lib/postgresql/data]

  # ─── AI ENGINE ─────────────────────────────────────────────────
  ai-engine:
    build: ./services/ai-engine
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@ai-db:5432/ai_db
      REDIS_URL: redis://redis:6379/2
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
    depends_on: [ai-db, redis, rabbitmq]

  ai-db:
    image: postgres:16
    volumes: [ai-data:/var/lib/postgresql/data]

  # ─── REPORTS ───────────────────────────────────────────────────
  reports:
    build: ./services/reports
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@reports-db:5432/reports_db
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      TRANSACTIONS_SERVICE_URL: http://transactions:8003
      INVOICE_SERVICE_URL: http://invoice:8002
      HR_SERVICE_URL: http://hr:8004
    depends_on: [reports-db, rabbitmq]

  reports-db:
    image: postgres:16
    volumes: [reports-data:/var/lib/postgresql/data]

  # ─── SETTINGS ──────────────────────────────────────────────────
  settings:
    build: ./services/settings
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@settings-db:5432/settings_db
    depends_on: [settings-db]

  settings-db:
    image: postgres:16
    volumes: [settings-data:/var/lib/postgresql/data]

  # ─── SHARED INFRASTRUCTURE ─────────────────────────────────────
  redis:
    image: redis:7-alpine

  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "15672:15672"              ← RabbitMQ UI at localhost:15672

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports:
      - "9001:9001"                ← MinIO UI at localhost:9001
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes: [minio-data:/data]

volumes:
  auth-data: 
  invoice-data: 
  transactions-data: 
  hr-data: 
  procurement-data: 
  vendors-data: 
  ai-data: 
  reports-data: 
  settings-data:
  minio-data:
```

---

## 17. All API Endpoints (Complete Reference)

All URLs go through the Gateway: `http://localhost:8000`

```
AUTH
  POST   /api/v1/auth/signup
  POST   /api/v1/auth/login
  POST   /api/v1/auth/refresh
  POST   /api/v1/auth/logout

DASHBOARD
  GET    /api/v1/transactions/kpis                       (today_revenue, monthly_revenue, etc.)
  GET    /api/v1/transactions/chart/revenue-vs-expenses
  GET    /api/v1/transactions/chart/cash-flow
  GET    /api/v1/transactions/chart/profit-margin
  GET    /api/v1/transactions/upcoming-payments
  GET    /api/v1/notifications

INVOICES
  GET    /api/v1/invoices                                (?status=Pending&limit=10)
  POST   /api/v1/invoices
  PUT    /api/v1/invoices/{id}
  GET    /api/v1/invoices/status-summary                 (Processed/Pending/Duplicate/Needs Review counts)
  POST   /api/v1/invoices/scan                           (multipart file upload)
  GET    /api/v1/invoices/scan/{job_id}                  (poll OCR result)
  POST   /api/v1/invoices/{id}/validate
  POST   /api/v1/invoices/{id}/send-to-accounting
  GET    /api/v1/invoices/sales
  POST   /api/v1/invoices/sales
  GET    /api/v1/invoices/sales/{id}/pdf

REVENUE
  GET    /api/v1/revenue                                 (?date_from=&date_to=)
  POST   /api/v1/revenue
  GET    /api/v1/revenue/sources                         (Retail/Wholesale/Online/Exports/Services breakdown)
  GET    /api/v1/revenue/top-customers                   (top 5 by PKR spend)

EXPENSES
  GET    /api/v1/expenses                                (?category=&status=)
  POST   /api/v1/expenses
  PUT    /api/v1/expenses/{id}/approve
  PUT    /api/v1/expenses/{id}/reject
  GET    /api/v1/expenses/categories                     (Salaries/Fuel/Raw Material etc.)

EMPLOYEES
  GET    /api/v1/employees
  POST   /api/v1/employees
  PUT    /api/v1/employees/{id}
  GET    /api/v1/employees/payroll
  POST   /api/v1/employees/payroll/process
  GET    /api/v1/employees/payroll/history

PROCUREMENT
  GET    /api/v1/procurement/requests
  POST   /api/v1/procurement/requests
  POST   /api/v1/procurement/requests/{id}/approve
  GET    /api/v1/procurement/requests/{id}/timeline
  GET    /api/v1/procurement/orders
  POST   /api/v1/procurement/orders
  PUT    /api/v1/procurement/orders/{id}/status
  GET    /api/v1/procurement/vendor-comparison
  GET    /api/v1/procurement/stats

VENDORS
  GET    /api/v1/vendors
  POST   /api/v1/vendors
  PUT    /api/v1/vendors/{id}
  GET    /api/v1/vendors/top                             (top 5 by PKR spend for chart)
  GET    /api/v1/vendors/{id}/invoices

AI
  POST   /api/v1/ai/chat                                 (message, session_id → AI response)
  GET    /api/v1/ai/insights                             (current AI insight cards)
  GET    /api/v1/ai/forecast                             (revenue forecast data)

REPORTS
  POST   /api/v1/reports/profit-loss
  POST   /api/v1/reports/balance-sheet
  POST   /api/v1/reports/cash-flow
  POST   /api/v1/reports/tax-summary
  POST   /api/v1/reports/sales
  POST   /api/v1/reports/purchases
  GET    /api/v1/reports/{id}/pdf
  GET    /api/v1/reports/{id}/excel

SETTINGS
  GET    /api/v1/settings/company
  PUT    /api/v1/settings/company
  GET    /api/v1/settings/automation
  PUT    /api/v1/settings/automation
  GET    /api/v1/settings/tax
  PUT    /api/v1/settings/tax
```

---

## 18. Standard Response Format

Every API response from every service uses the same format. This makes the frontend code consistent.

**Success response:**
```json
{
    "data": { ... },
    "message": "Invoice created successfully",
    "trace_id": "abc-123-xyz"
}
```

**Error response (all errors, any service):**
```json
{
    "error": "Validation Error",
    "detail": "Amount must be greater than 0",
    "code": 422,
    "trace_id" : "abc-123-xyz"
}
```

The `trace_id` is created at the Gateway and passed through every service. If something breaks, you search all service logs for that `trace_id` to see the full picture.

---

## 19. Authentication Flow

```
1. User: POST /api/v1/auth/login { "email": "ayesha@company.pk", "password": "..." }
   ↓
2. Gateway: sees /auth route → passes straight through (no JWT check on auth routes)
   ↓
3. Auth Service: verifies password (Argon2), creates:
   - Access token (JWT, expires in 15 minutes)
   - Refresh token (random string, expires in 7 days, stored in DB)
   Returns: { access_token: "eyJ...", refresh_token: "...", expires_in: 900 }
   ↓
4. Frontend stores access_token in memory, refresh_token in httpOnly cookie
   ↓
   ─── Every protected request ───
   ↓
5. Frontend: GET /api/v1/transactions/kpis
   Headers: Authorization: Bearer eyJ...
   ↓
6. Gateway: verifies JWT signature using JWT_SECRET_KEY
   - Invalid → 401, stop
   - Expired → 401, frontend auto-refreshes using refresh token
   - Valid → extracts: { user_id: "123", company_id: "456", role: "admin" }
   Injects headers: X-User-ID: 123, X-Company-ID: 456, X-User-Role: admin
   ↓
7. Transactions Service: receives request with X-Company-ID header
   Queries: SELECT * FROM revenue WHERE company_id = '456'
   Returns company-specific data only
```

Key point: **Only the Gateway verifies JWT.** Downstream services trust the `X-Company-ID` header. This means every DB query automatically filters by the right company — no data leaks between companies.

---

## 20. Security Checklist for a Financial App

This is a money-related platform for businesses. Security is non-negotiable.

| Area | Requirement |
|---|---|
| **Passwords** | Argon2 hashing — strongest standard |
| **Tokens** | 15-min access tokens + 7-day httpOnly refresh tokens |
| **HTTPS** | All traffic over HTTPS in production |
| **Input Validation** | Pydantic validates every request — bad input never reaches business logic |
| **Rate Limiting** | 100 req/min per IP, 1000 req/min per authenticated user (Redis) |
| **File Uploads** | Validate type (PDF/JPG/PNG only), max 20MB, scan for malware |
| **SQL Injection** | SQLAlchemy ORM — never raw SQL strings |
| **Multi-tenancy** | Every query filters by `company_id` — companies never see each other's data |
| **Secrets** | All API keys in environment variables — never in code or git |
| **CORS** | Strict allowlist — only the production frontend domain and `localhost:8080` |
| **Error Messages** | Internal errors (DB errors, stack traces) never sent to client |
| **NTN Data** | Pakistani National Tax Number stored encrypted |

---

## 21. CI/CD — Per Service Pipelines

Each service has its own GitHub Actions workflow. Only triggered when that service's code changes.

```yaml
# .github/workflows/invoice-ci.yml

name: Invoice Service CI

on:
  push:
    paths:
      - 'backend/services/invoice/**'     ← Only runs if invoice code changed
      - 'backend/libs/shared/**'          ← Or if shared library changed
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        working-directory: backend/services/invoice
        run: |
          pip install -e ".[dev]"
          pytest tests/ -v --cov=app

  build:
    needs: test
    steps:
      - name: Build Docker image
        run: docker build -t finpilot/invoice:${{ github.sha }} ./backend/services/invoice

  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Deploy Invoice Service only
        run: echo "Deploy to production"
```

**Why separate pipelines?** Changing the AI Engine should not redeploy HR Service. Independent deployment = truly independent services.

---

## 22. Development Setup — Start Here

```bash
# Step 1: Clone the repo (already done)
cd finpilot-ai-showcase

# Step 2: Create the backend folder structure
mkdir -p backend/services/{gateway,auth,invoice,transactions,hr,procurement,vendors,ai-engine,reports,settings}
mkdir -p backend/libs/shared/events
mkdir -p backend/infra

# Step 3: Copy env files
cp backend/services/invoice/.env.example backend/services/invoice/.env
# ... repeat for each service

# Step 4: Add your API keys
# Edit backend/services/ai-engine/.env:
# OPENAI_API_KEY=sk-...

# Step 5: Run everything
docker compose -f backend/infra/docker-compose.yml up --build

# Services now available at:
# http://localhost:8000      ← API Gateway (use this for all requests)
# http://localhost:8000/docs ← Swagger UI (auto-generated by FastAPI)
# http://localhost:15672     ← RabbitMQ dashboard
# http://localhost:9001      ← MinIO (file storage) dashboard
```

---

## 23. What You Build First (Recommended Order)

Build in this order so you always have a working system:

1. **Auth Service** — Login/Signup. Get JWT working. Without this, nothing else is secure.
2. **API Gateway** — Routes and JWT verification. Connect Gateway to Auth.
3. **Transactions Service** — Dashboard KPIs, revenue, expenses. This powers the main dashboard.
4. **Vendors Service** — Simple CRUD. Powers the Vendors page and chart data.
5. **Invoice Service** — Invoice CRUD first (without AI). Then add AI OCR.
6. **HR Service** — Employees and payroll. Straightforward CRUD.
7. **Procurement Service** — Purchase requests and orders. Slightly complex due to approval flow.
8. **AI Engine Service** — Invoice OCR + AI chat. Add OpenAI/Anthropic calls here.
9. **Reports Service** — PDF generation. Most complex — do this last.
10. **Settings Service** — Company profile. Simple CRUD.

---

## 24. Summary — Everything in One Table

| Decision | Choice | Why |
|---|---|---|
| **Architecture** | Microservices (9 services + gateway) | Each page's domain is truly independent |
| **Repo** | Monorepo (backend/ + frontend/ in same repo) | Solo developer — easier to manage |
| **Internal structure** | Flat for simple services, layered for complex | Match complexity to actual need |
| **Database** | PostgreSQL 16, one per service | Financial data needs relational DB; total isolation |
| **Service communication** | REST (sync) + RabbitMQ (async) | REST for UI responses, queue for slow work |
| **Authentication** | JWT verified at Gateway only | One place for auth logic |
| **File storage** | MinIO local / S3 production | Invoice PDFs/images need object storage |
| **AI provider** | OpenAI GPT-4o or Anthropic Claude | For invoice OCR and chat assistant |
| **Local dev** | Docker Compose | One command, everything runs |
| **Currency** | PKR (Pakistani Rupees) | Pakistani SME platform |
| **Target market** | Pakistani SMEs | All data models, tax logic, vendor terms for PK market |

---

## Sources & References

- FastAPI Best Practices: https://github.com/zhanymkanov/fastapi-best-practices
- FastAPI Full-Stack Template: https://github.com/fastapi/full-stack-fastapi-template
- FastAPI Production Structure 2026: https://www.zestminds.com/blog/fastapi-project-structure/
- FastAPI Folder Structure Guide: https://fastro.ai/blog/fastapi-folder-structure
- Microservices API Gateway Pattern: https://microservices.io/patterns/apigateway.html
- Saga Pattern for distributed transactions: https://microservices.io/patterns/data/saga.html
- FastAPI Microservices Communication: https://medium.com/algomart/inter-service-communication-in-fastapi-a-professional-guide-to-rest-grpc-and-messaging-patterns-69c0162b1515
- FastAPI for Microservices 2025: https://talent500.com/blog/fastapi-microservices-python-api-design-patterns-2025/

---

*Architecture designed for the FinPilot AI backend*
*Prepared: August 12, 2026*
*Frontend: React + TypeScript + Vite + TanStack Router (already built)*
*Backend: FastAPI + PostgreSQL + RabbitMQ + Redis (target architecture; current hand-offs use HTTP and RabbitMQ is not deployed)*
*Market: Pakistani SMEs | Currency: PKR*