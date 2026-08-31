# Slack Connector Integration — Design Spec

*Prepared: 2026-08-18*
*Status: Approved for planning*

## 1. Summary

FinPilot AI's backend is being built out as a set of FastAPI microservices per
`FinPilot_AI_Backend_Architecture_Report (3).md`. This spec covers the first
microservice to be built: a **Slack Connector service** that lets a FinPilot
company connect their Slack workspace, discovers and categorizes files shared
in it, and lets the user manually push invoice/receipt-looking files into the
existing Invoice Service OCR pipeline.

The backend logic already exists as a complete, standalone, production-ready
microservice at `D:\projects\slack-connector` (see
`docs/Slack Connector - Implementation Progress Report.md` and
`docs/integration-notes.md` in that repo). This spec adapts that existing
service to FinPilot's architecture rather than designing it from scratch:
multi-tenant `company_id` scoping, Gateway-issued JWT auth instead of a
stubbed auth layer, and a new frontend surface inside FinPilot's own
shadcn/Tailwind app instead of the connector's standalone Mantine UI.

## 2. Goals

- Add a 10th backend service, `slack-connector`, that fits FinPilot's existing
  patterns: own Postgres database, Gateway-only public access, JWT verified
  upstream, `X-Company-ID` scoping downstream.
- Let a logged-in FinPilot user connect exactly one Slack workspace to their
  company via OAuth.
- Discover files shared in that workspace (channels, DMs, threads — whatever
  the granted scopes allow), categorize them with the existing rule-based
  classifier, and store metadata + bytes.
- Let the user browse those files, filter by category, correct
  mis-categorization, and manually send an invoice/receipt-looking file into
  the existing Invoice Service scan pipeline (`POST /invoices/scan`).
- Surface all of this from a new "Connected Apps" tab on the existing
  `/app/settings` page, built with FinPilot's existing component library.

## 3. Non-goals (deferred, not part of this slice)

- Automatic feed of Slack files into the OCR pipeline without a manual click.
- Events API incremental sync (full syncs only, same as the source project).
- Celery/RabbitMQ-backed background jobs — sync stays on
  `asyncio.create_task` fire-and-forget for now.
- Multi-workspace-per-company or Enterprise Grid support.
- A standalone Slack-connector frontend — the existing `frontend/connector-ui`
  Mantine app in the source repo is reference material only, not something we
  deploy.

## 4. Service placement

```
backend/services/slack-connector/
├── app/
│   ├── main.py
│   ├── api/
│   │   └── v1/
│   │       ├── auth.py            ← /connect, /callback
│   │       └── sync.py            ← sync + files endpoints
│   ├── schemas/
│   ├── services/
│   │   ├── slack/                 ← oauth.py, client.py, discovery.py
│   │   ├── categorization/        ← service.py, rules.yaml
│   │   ├── download_manager.py
│   │   ├── rate_limiter.py
│   │   ├── sync_orchestrator.py
│   │   ├── reconciliation.py
│   │   └── scanner_bridge.py      ← NEW: calls Invoice Service
│   ├── repositories/
│   ├── models/
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── security.py            ← token cipher + state encryption
│   │   └── logging.py
├── tests/
├── alembic/
├── Dockerfile
├── pyproject.toml
└── .env.example
```

Port: **8010**. Route prefix through the Gateway: **`/api/v1/slack/*`**.

### 4.1 Porting plan from `D:\projects\slack-connector`

Direct ports (business logic is domain-generic, no changes needed beyond
import paths):

| Source | Destination |
|---|---|
| `backend/connector-service/app/services/slack/discovery.py` | same |
| `backend/connector-service/app/services/slack/client.py` | same |
| `backend/connector-service/app/services/slack/oauth.py` | adapted — see §6 |
| `backend/connector-service/app/services/categorization/*` | same |
| `backend/connector-service/app/services/download_manager.py` | same |
| `backend/connector-service/app/services/rate_limiter.py` | same |
| `backend/connector-service/app/services/sync_orchestrator.py` | same |
| `backend/connector-service/app/services/reconciliation.py` | same |
| `backend/connector-service/app/core/logging.py` (`SecretRedactingFormatter`) | same — matches FinPilot's structlog JSON logging requirement |
| `backend/connector-service/app/core/security.py` (`TokenCipher`) | same |

Adapted (multi-tenancy / auth rework):

| Source | Change |
|---|---|
| `app/models/installation.py` | add `company_id: UUID` (unique, not null) |
| `app/models/workspace.py` | unchanged |
| `app/api/routes/auth.py` | rewritten per §6 — state now carries `company_id` |
| `app/api/routes/sync.py` | endpoints now resolve `installation` via `X-Company-ID` header instead of an `installation_id` query param passed by an untrusted client |
| `app/core/config.py` | add FinPilot's shared JWT verification settings (from `backend/libs/shared`) |

New:

| File | Purpose |
|---|---|
| `app/services/scanner_bridge.py` | `POST /files/{id}/send-to-scanner` logic — see §7 |

Not ported: `frontend/connector-ui` (reference only), the connector's own
mocked/stubbed auth middleware (replaced by FinPilot's Gateway-header trust
model).

## 5. Data model changes

`installation` table gains:

```
company_id UUID NOT NULL UNIQUE
```

Every repository query in this service filters by `company_id` (sourced from
the `X-Company-ID` header the Gateway injects), the same pattern every other
FinPilot service uses. No other schema changes — `workspace`, `conversation`,
`app_user`, `file`, `sync_job`, `sync_cursor` are carried over unchanged from
the source project (see `docs/integration-notes.md` in the source repo for
full column listings).

A new Alembic revision in this service adds the `company_id` column and its
unique constraint on top of the ported `20260813_01`/`02`/`03` revisions.

## 6. OAuth flow through the Gateway

Slack's redirect is a plain browser navigation — it cannot carry an
`Authorization` header, so the Gateway's normal JWT-verification cannot apply
to the callback leg. The flow:

1. Frontend calls `GET /api/v1/slack/connect` **through the Gateway**,
   authenticated (JWT in header, as usual).
2. Gateway verifies the JWT, injects `X-Company-ID`, forwards to the
   connector service.
3. Connector service builds the Slack `authorize` URL. The `state` parameter
   is the existing Phase-1 encrypted-state blob, extended to also carry the
   `company_id` (previously it only carried a CSRF nonce validated against an
   HTTP-only cookie — that mechanism is kept, just with `company_id` added to
   the encrypted payload).
4. Response is a redirect (or a JSON `{url}` the frontend navigates to) —
   browser goes to `slack.com`.
5. User approves. Slack redirects the browser to
   `GET /api/v1/slack/callback`, which is registered as a **Gateway route
   that skips JWT verification** — the same allowlist mechanism the Gateway
   already uses for `/api/v1/auth/*` (§19 of the architecture report).
6. Callback handler decrypts `state`, recovers `company_id`, validates the
   CSRF cookie, exchanges the code via `oauth.v2.access`, encrypts the bot
   token (Fernet, unchanged from the source project), and upserts
   `installation` scoped to that `company_id`.
7. Redirects the browser to the FinPilot frontend's Settings → Connected Apps
   tab (`FRONTEND_BASE_URL/app/settings?tab=connected-apps`), replacing the
   source project's redirect to its own `connector-ui` Connected page.

## 7. Send-to-Scanner bridge

`POST /api/v1/slack/files/{id}/send-to-scanner`:

- Loads the file row (scoped by `company_id`), confirms it's `downloaded`.
- Fetches the bytes from this service's own S3/MinIO storage.
- Calls Invoice Service's existing `POST /invoices/scan` over `httpx`,
  multipart, forwarding `X-Company-ID` / `X-User-ID` — i.e. it looks exactly
  like a normal manual upload to Invoice Service. This service never touches
  Invoice Service's database (Rule 1).
- Returns Invoice Service's `{job_id, status: "processing"}` response
  unchanged so the frontend can reuse the scanner's existing polling UI if
  desired, or just toast success and let the user check the Scanner page.

`INVOICE_SERVICE_URL` is added to this service's env, same pattern as
AI Engine Service calling Vendors/Invoice/Transactions Service URLs in the
architecture report (§15).

## 8. API surface (through the Gateway)

```
GET    /api/v1/slack/connect                     (starts OAuth, requires JWT)
GET    /api/v1/slack/callback                     (Slack redirect target, JWT-exempt)
GET    /api/v1/slack/status                        (connected? workspace name, scopes, last sync)
POST   /api/v1/slack/sync                          (start a sync job)
GET    /api/v1/slack/sync/{sync_id}                 (poll sync job status)
GET    /api/v1/slack/files                          (?category=&skip=&limit=)
GET    /api/v1/slack/files/{id}                     (metadata + signed preview URL)
PATCH  /api/v1/slack/files/{id}/category            (manual override)
POST   /api/v1/slack/files/{id}/retry                (re-queue failed download)
POST   /api/v1/slack/files/{id}/send-to-scanner      (NEW — §7)
DELETE /api/v1/slack/installation                    (disconnect workspace)
```

All routes except `/connect` and `/callback` require the standard Gateway JWT
+ `X-Company-ID` flow. `installation_id` is no longer a client-supplied query
param (as it was in the source project's mocked-auth version) — it's resolved
server-side from `company_id`, since V1 is one installation per company.

Response envelope matches FinPilot's standard format (§18 of the architecture
report): `{data, message, trace_id}` / `{error, detail, code, trace_id}`.

## 9. Infrastructure

Added to `backend/infra/docker-compose.yml`:

```yaml
slack-connector:
  build: ./services/slack-connector
  environment:
    DATABASE_URL: postgresql+asyncpg://postgres:postgres@slack-connector-db:5432/slack_connector_db
    REDIS_URL: redis://redis:6379/3
    S3_ENDPOINT_URL: http://minio:9000
    S3_BUCKET_NAME: slack-connector-files
    INVOICE_SERVICE_URL: http://invoice:8002
    JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    SLACK_CLIENT_ID: ${SLACK_CLIENT_ID}
    SLACK_CLIENT_SECRET: ${SLACK_CLIENT_SECRET}
    SLACK_SIGNING_SECRET: ${SLACK_SIGNING_SECRET}
    SLACK_REDIRECT_URI: ${SLACK_REDIRECT_URI}
    TOKEN_ENCRYPTION_KEY: ${TOKEN_ENCRYPTION_KEY}
  depends_on: [slack-connector-db, redis, minio]

slack-connector-db:
  image: postgres:16
  environment: { POSTGRES_DB: slack_connector_db, POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres }
  volumes: [slack-connector-data:/var/lib/postgresql/data]
```

Reuses the shared `redis` (DB index 3, unused by other services per the
report's existing `0`/`1`/`2` assignments) and shared `minio` (new bucket
`slack-connector-files`, following the same per-service-prefix convention the
report already uses for invoice file storage). No second Redis/MinIO
instance — Rule 1 is about databases, not shared cache/object storage.

Gateway gets `SLACK_CONNECTOR_SERVICE_URL: http://slack-connector:8010` added
to its env and routing table, plus `/api/v1/slack/callback` added to its
JWT-exempt path allowlist alongside `/api/v1/auth/*`.

## 10. Frontend

New **"Connected Apps"** tab added to the existing `Tabs` in
[app.settings.tsx](../../src/routes/app.settings.tsx), alongside `Company`,
`AI Automation`, `Appearance`:

- **Not connected state**: card explaining what the Slack connector does, a
  "Connect Slack" button that calls `GET /api/v1/slack/connect` and navigates
  to the returned URL.
- **Connected state**: workspace name, granted scopes, last synced time, a
  "Sync now" button (`POST /api/v1/slack/sync`, polls
  `GET /api/v1/slack/sync/{id}` the same 2-second-interval pattern the source
  project's `SyncProgressPage.tsx` used), and a "Disconnect" button.
- **"Browse synced files"** button opens the existing `Sheet` component
  (`src/components/ui/sheet.tsx`) as a slide-over panel: search, category
  filter, file grid (rebuilt with FinPilot's `Card`/`Badge`/`Table`
  primitives instead of Mantine), and a "Send to Scanner" action on each
  invoice/receipt/financial-categorized file that calls the bridge endpoint
  from §7 and toasts success via the existing `sonner` toast pattern already
  used elsewhere in the app (see `app.scanner.tsx`).

No new TanStack route is added — everything lives inside `/app/settings` via
tab state and the Sheet panel, matching the "Connected Apps" placement
decision.

Data fetching uses TanStack Query, consistent with the rest of the frontend
integration plan in §10 of the architecture report.

## 11. Security

Carried over unchanged from the source project (already meets FinPilot's
§20 security checklist):

- Slack bot/user tokens encrypted at rest (Fernet).
- `SecretRedactingFormatter` strips tokens/secrets from all log lines.
- Signed, short-TTL S3 URLs for file preview/download — raw S3 keys and
  Slack's `url_private_download` never reach the client.
- CORS/redirect URIs restricted to the FinPilot frontend origin.

New for this integration:

- `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, Slack app credentials all via env
  vars / secrets manager — never committed (`.env.example` only).
- Every query scoped by `company_id`, resolved server-side from the Gateway's
  injected header — never trusted from client input. This closes the gap the
  source project's own integration notes flagged: "installation.workspace_id
  linked to a user_id or organization_id in the larger system."

## 12. Testing

Ports the source project's existing test suite
(`tests/unit/test_oauth.py`, `test_security.py`, `test_phase3_hardening.py`)
with adjustments for the `company_id`-scoped queries and the new OAuth state
shape. Adds:

- Unit test for `scanner_bridge.py` (mocked Invoice Service HTTP call).
- Unit test for the Gateway JWT-exempt callback path.
- Integration test: connect → sync → categorize → send-to-scanner happy path,
  Slack API and Invoice Service both mocked.

## 13. Build order

1. Scaffold `backend/services/slack-connector`; port models + business logic
   per §4.1; add `company_id` migration.
2. Rework OAuth (§6): encrypted state carries `company_id`, Gateway
   JWT-exempt callback route.
3. Add `scanner_bridge.py` and the send-to-scanner endpoint (§7).
4. Docker Compose + Gateway routing (§9).
5. Frontend: Connected Apps tab + file-browser Sheet (§10).
6. Port and adapt the test suite (§12).

## 14. Open items carried forward (explicitly deferred, not blockers)

These match the source project's own "Left for Phase 3 / future work" list
and remain deferred here too:

- Events API incremental sync.
- Celery/RabbitMQ-backed sync jobs (currently `asyncio.create_task`).
- Content-based/OCR/LLM categorization (rule-based only for now).
- Multi-workspace-per-company support.
- Automatic (non-manual) feed into the OCR pipeline — candidate fast-follow
  once this slice is live and validated.
