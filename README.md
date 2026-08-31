# FinPilot AI

AI accounting automation for Pakistani SMEs — *automate bookkeeping, extract invoices, analyze finances.*

A React dashboard plus a FastAPI microservice backend. The frontend UI is complete and runs on
realistic dummy data; the backend is being built service by service, replacing that dummy data
with real APIs as each one lands.

---

## Repository layout

```
finpilot-ai-showcase/
├── frontend/                  React + TanStack Start dashboard
│   ├── src/
│   │   ├── routes/            one file per page
│   │   ├── components/        UI components (shadcn/ui in components/ui)
│   │   └── lib/               API clients + dummy data
│   ├── package.json
│   └── vite.config.ts         includes dev proxies to backend services
│
├── backend/
│   ├── services/
│   │   ├── gateway/           The front door — JWT verification (port 8000)
│   │   ├── auth/              Signup, login, JWT issuance (port 8001)
│   │   ├── slack-connector/   Slack file discovery service (port 8010)
│   │   └── email-connector/   Gmail OAuth + mailbox connector (port 8011)
│   ├── libs/shared/           JWT verification shared by every service
│   ├── infra/
│   │   └── docker-compose.yml Postgres + Redis + MinIO + the service
│   └── docs/
│       └── api-contracts.md   endpoint reference
│
└── docs/                      specs, plans, design history
```

The layout follows section 6 of `FinPilot_AI_Backend_Architecture_Report`.

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

### Backend — Slack Connector

Brings up Postgres, Redis, MinIO, the API and the Celery worker together:

```bash
cd backend/infra
docker compose up --build -d
curl http://localhost:8010/health          # → {"status":"ok"}
```

First you need real credentials:

```bash
cd backend/services/slack-connector
cp .env.example .env
# then fill in SLACK_CLIENT_ID / SLACK_CLIENT_SECRET / SLACK_SIGNING_SECRET from
# https://api.slack.com/apps, and generate TOKEN_ENCRYPTION_KEY with:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`.env` is gitignored and must never be committed. Details, local (non-Docker) setup and the
security model are in
[`backend/services/slack-connector/README.md`](backend/services/slack-connector/README.md).

### Backend — Email Connector (Phase 1: OAuth foundation)

Same `docker compose up` brings this up too:

```bash
curl http://localhost:8011/health          # → {"status":"ok"}
```

Needs a Google Cloud OAuth client before `/connect` can complete a real consent flow:

```bash
cd backend/services/email-connector
cp .env.example .env
# then fill in GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from Google Cloud Console →
# APIs & Services → Credentials, and generate TOKEN_ENCRYPTION_KEY the same way as Slack's.
```

Sync, attachment download, and filtering are not built yet — see
[`docs/email-connector-plan.md`](docs/email-connector-plan.md) for the phased plan and
[`backend/services/email-connector/README.md`](backend/services/email-connector/README.md)
for what's different from the Slack connector this was copied from.

---

## Services

| Service | Port | Status | Powers |
| --- | --- | --- | --- |
| **Slack Connector** | 8010 | ✅ built | Documents page, Connected Apps tab, file browser, preview, send-to-scanner |
| Email Connector | 8011 | 🚧 Gmail OAuth ✅ live-tested with a real account; sync not built yet | Connected Apps tab, Documents page — Email column (connects; no files yet) |
| WhatsApp Connector | 8012 | planned | Documents page — WhatsApp column |
| **Auth** | 8001 | ✅ built | Signup, login, JWT issuance, refresh tokens |
| **Gateway** | 8000 | ✅ built | The front door — verifies JWTs, injects identity, routes to services |
| Invoice | 8002 | planned | Invoice Scanner, Invoice Generator |
| Transactions | 8003 | planned | Revenue Manager, Expenses |
| HR | 8004 | planned | Employees, payroll |
| Procurement | 8005 | planned | Purchase requests/orders |
| Vendors | 8006 | planned | Vendor management |
| AI Engine | 8007 | planned | OCR, AI Assistant, insights |
| Reports | 8008 | planned | Report generation |
| Settings | 8009 | planned | Company configuration |

Pages whose service is still `planned` render dummy data from `frontend/src/lib/data.ts`.

### Documents page

`/app/documents` shows every document FinPilot has collected, one column per connector.
Slack is live; Email and WhatsApp render a "planned" column describing what they will
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
| [`CHANGELOG.md`](CHANGELOG.md) | Running project report — what was added, fixed and changed, newest first |
| [`FinPilot_AI_Backend_Architecture_Report`](FinPilot_AI_Backend_Architecture_Report%20(3).md) | Full architecture: services, folder structure, endpoints, deployment |
| [`backend/docs/api-contracts.md`](backend/docs/api-contracts.md) | Live endpoint reference |
| [`backend/services/auth/README.md`](backend/services/auth/README.md) | Auth Service: endpoints, token handling, security decisions |
| [`backend/services/slack-connector/README.md`](backend/services/slack-connector/README.md) | Running, configuring and extending the Slack service |
| [`docs/original-design-brief.md`](docs/original-design-brief.md) | The original frontend design brief this UI was generated from |
| [`docs/email-connector-plan.md`](docs/email-connector-plan.md) | Build plan and research guide for the Email connector |
| [`docs/selective-sync-plan.md`](docs/selective-sync-plan.md) | Plan for choosing which channels/DMs to sync, and grouping documents by conversation |
| [`docs/superpowers/`](docs/superpowers/) | Specs and implementation plans per feature |
