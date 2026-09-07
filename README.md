# FinPilot AI

AI accounting automation for Pakistani SMEs — *automate bookkeeping, extract invoices, analyze finances.*

A React dashboard plus a FastAPI microservice backend. The frontend UI is complete, and the
backend services are implemented incrementally with real APIs replacing the original dummy data.

---

## Repository layout

```
finpilot-ai-showcase/
├── frontend/                  React 19 + TanStack Start dashboard
│   ├── src/
│   │   ├── routes/            route pages (dashboard, scanner, records, documents, hr, etc.)
│   │   ├── components/        shadcn/ui, scanner with camera/crop, review dialog, preview
│   │   └── lib/               API clients, query hooks, blur detection
│   ├── package.json
│   └── vite.config.ts         dev server and proxy config
│
├── backend/
│   ├── services/
│   │   ├── gateway/           Front door — JWT verification & routing (port 8000)
│   │   ├── auth/              User authentication & company management (port 8001)
│   │   ├── invoice-service/   Invoices, receipts, categorization & cashbook (port 8002)
│   │   ├── transactions-service/ Revenue and expense tracking (port 8003)
│   │   ├── hr-service/        Employee directory & role assignments (port 8004)
│   │   ├── procurement-service/   Purchase requests, orders & quotes (port 8005)
│   │   ├── vendors-service/       Vendor profiles and reconciliation (port 8006)
│   │   ├── ai-engine/         Deterministic candidate selection & validation (port 8007)
│   │   ├── paddleocr/         OCR text & bounding box extraction (port 8008)
│   │   ├── settings-service/      Company and automation settings (port 8009)
│   │   ├── slack-connector/   Slack file discovery & sync (port 8010)
│   │   └── email-connector/   Gmail OAuth & mailbox connector (port 8011)
│   │   ├── documents-service/     Browser-uploaded document library (port 8013)
│   │   └── reports-service/       Financial report generation (port 8014)
│   ├── libs/
│   │   ├── shared/            Shared auth & JWT dependencies
│   │   ├── ocr/               Image preprocessing & OCR rendering utilities
│   │   └── invoice_extraction/ Deterministic candidate extraction rules
│   └── infra/
│       └── docker-compose.yml Postgres, Redis, MinIO, and core microservices
│
└── docs/
    ├── FinPilot_AI_Backend_Architecture_Report (3).md
    └── superpowers/           Feature specifications and implementation plans
```

The layout follows section 6 of [`docs/FinPilot_AI_Backend_Architecture_Report (3).md`](docs/FinPilot_AI_Backend_Architecture_Report%20(3).md).

---

## Quick start

### Frontend

Requires [bun](https://bun.sh) (this repo uses `bun.lock`; don't use npm — it will produce a
conflicting `package-lock.json`).

```bash
cd frontend
bun install
bun run dev
```

Opens on `http://localhost:8080`. Every page works standalone against dummy data, so you do
not need the backend running to browse the UI.

### Backend Microservices

Brings up Postgres, Redis, MinIO, and the backend services together:

```bash
cd backend/infra
docker compose up --build -d
```

Check health across services:

```bash
curl http://localhost:8000/health          # Gateway → {"status":"ok"}
curl http://localhost:8001/health          # Auth → {"status":"ok"}
curl http://localhost:8002/health          # Invoice Service → {"status":"ok"}
curl http://localhost:8004/health          # HR Service → {"status":"ok"}
curl http://localhost:8007/health          # AI Engine → {"status":"ok"}
curl http://localhost:8008/health          # PaddleOCR → {"status":"ok"}
curl http://localhost:8010/health          # Slack Connector → {"status":"ok"}
curl http://localhost:8011/health          # Email Connector → {"status":"ok"}
```

---

## Services

| Service | Port | Status | Powers |
| --- | --- | --- | --- |
| **Gateway** | 8000 | ✅ built | The front door — verifies JWTs, injects identity headers, routes to services |
| **Auth** | 8001 | ✅ built | Signup, login, JWT issuance, company scoping, refresh tokens |
| **Invoice Service** | 8002 | ✅ built | Scanner uploads, OCR processing, category classification, Saved Records cashbook, Needs Review workflow, PDF export |
| Transactions | 8003 | ✅ built | Expenses, approvals, dashboard KPIs and trends |
| **HR Service** | 8004 | ✅ built | Employee directory, role and department assignments |
| Procurement | 8005 | ✅ built | Purchase requests, purchase orders and vendor quotes |
| Vendors | 8006 | ✅ built | Vendor management, spend tracking and reconciliation |
| **AI Engine** | 8007 | ✅ built | Deterministic candidate selection, financial validation, vendor/total extraction |
| **PaddleOCR** | 8008 | ✅ built | Document OCR text and bounding-box extraction service |
| **Settings** | 8009 | ✅ built | Company profile, tax configuration and automation settings |
| **Slack Connector** | 8010 | ✅ built | Documents page, Connected Apps tab, file browser, preview, send-to-scanner |
| **Email Connector** | 8011 | ✅ built | Gmail OAuth integration, mailbox connector and document sync |
| WhatsApp Connector | 8012 | 🚧 planned | Documents page — WhatsApp channel |
| **Documents Service** | 8013 | ✅ built | Browser-uploaded document library |
| **Reports** | 8014 | ✅ built | P&L, cash flow, tax, sales and purchase reports |

Pages whose service is still `planned` render dummy data from `frontend/src/lib/data.ts`.

### Documents page

`/app/documents` shows every document FinPilot has collected, one column per connector.
Slack and Email are live; WhatsApp renders a "planned" column describing what it will
collect, rather than inventing documents for a source that does not exist yet.

Each file has a **Preview** button that opens it inside the app — PDFs in the browser's
native viewer, images inline — by streaming from the connector's
`GET /files/{id}/content` endpoint. Previewing goes through the service rather than a
signed storage URL, so it stays behind the company-scoping check and no S3 URL reaches
the browser.

Adding a connector is three steps: build the service against the shared connector
contract (architecture report §5.11), add an adapter in `frontend/src/lib/documents.ts`,
and flip its status to `available`. The page itself needs no changes.

### Accounts and sign-in

Sign up at `/signup` — that creates your company and makes you its admin — then sign in at
`/login`. The access token is held in memory only and the refresh token is an httpOnly
cookie, so no session is readable by page scripts.

⚠️ **`JWT_SECRET_KEY` must be identical in the Auth Service and every service that verifies
tokens.** If they differ, logins succeed but every downstream call returns 401.

### How requests flow

```
browser → Gateway :8000 → verifies JWT → injects X-Company-ID → service
```

The Gateway is the only component that verifies tokens and the only port that needs
exposing. It **strips any identity headers the caller sent** and rewrites them from the
verified claims — downstream services trust `X-Company-ID`, so forwarding a client-supplied
one would let anyone read another company's data.

Two routes are deliberately open, because a browser navigation cannot carry a bearer token:
`/api/v1/auth/*` (you cannot present a token before you have one) and
`/api/v1/slack/callback` (Slack redirects the user's browser there; it is protected by an
encrypted, short-lived `state` and an httpOnly nonce cookie instead).

⚠️ **The frontend dev proxy points at the Gateway**, so `bun run dev` needs the Gateway
running. Starting only the connector will not work.

### Known interim shortcut

Services can still accept an **unverified** `X-Company-ID` header (or `DEFAULT_COMPANY_ID`)
when no token is present, so the API stays usable without signing in during development.
That fallback is controlled by `TRUST_COMPANY_HEADER`, is set to `false` in the composed
stack, and is **refused in production** — a service will not start with `APP_ENV=production`
and `TRUST_COMPANY_HEADER=true`.

---

## Tech stack

**Frontend** — React 19, TanStack Start (SSR) + TanStack Router, TanStack Query, Vite,
Tailwind CSS v4, shadcn/ui, Recharts, lucide-react, sonner. TypeScript in strict mode
(including `exactOptionalPropertyTypes`).

**Backend** — Python 3.11+, FastAPI, SQLAlchemy 2 (async) + asyncpg, Alembic, Celery + Redis,
PostgreSQL 16, MinIO (S3-compatible), Pydantic v2, pytest.

---

## Development notes

Run the frontend typecheck, lint and backend tests before committing:

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit
cd backend/services/slack-connector && .venv/Scripts/pytest tests/ -v
cd backend/services/email-connector && .venv/Scripts/pytest tests/ -v
cd backend/services/gateway && .venv/Scripts/pytest tests/ -v
```

Each backend service owns its own database, migrations and dependencies — no shared schema,
no cross-service database access. Services talk over HTTP.

`git config core.autocrlf` is `true` on Windows checkouts while Prettier expects LF, so
`bun run lint` reports line-ending errors on files it did not author. Existing errors are
pre-existing; don't mass-reformat to "fix" them.

---

## Documentation

| Document | What it covers |
| --- | --- |
| [`FinPilot_AI_Backend_Architecture_Report`](FinPilot_AI_Backend_Architecture_Report%20(3).md) | Full architecture: services, folder structure, endpoints, deployment |
| [`backend/docs/api-contracts.md`](backend/docs/api-contracts.md) | Live endpoint reference |
| [`backend/services/auth/README.md`](backend/services/auth/README.md) | Auth Service: endpoints, token handling, security decisions |
| [`backend/services/slack-connector/README.md`](backend/services/slack-connector/README.md) | Running, configuring and extending the Slack service |
| [`docs/original-design-brief.md`](docs/original-design-brief.md) | The original frontend design brief this UI was generated from |
| [`docs/email-connector-plan.md`](docs/email-connector-plan.md) | Build plan and research guide for the Email connector |
| [`docs/selective-sync-plan.md`](docs/selective-sync-plan.md) | Plan for choosing which channels/DMs to sync, and grouping documents by conversation |
| [`docs/superpowers/`](docs/superpowers/) | Specs and implementation plans per feature |
