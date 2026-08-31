# Slack Connector Service

Connects a FinPilot company's Slack workspace, discovers files shared in conversations,
classifies them (invoice / receipt / contract / …), stores them in S3, and lets a user push
the financial ones into FinPilot's AI Invoice Scanner.

**Port:** `8010` · **Prefix:** `/api/v1/slack` · **Database:** `slack_connector_db`

---

## How it works

```
Slack OAuth  ──▶  installation (1 per company, token encrypted at rest)
                        │
                        ▼
              DiscoveryService ── walks conversations, lists files
                        │
                        ▼
           CategorizationService ── rule-based classify (rules.yaml)
                        │
                        ▼
              DownloadManager ── fetches bytes, stores in S3/MinIO
                        │
                        ▼
              ScannerBridge ── POSTs a file to the Invoice Service on request
```

Discovery, download and classification run in a Celery worker, not in the request path —
a sync over a large workspace takes minutes, so `POST /sync/` returns a job id and the
frontend polls `GET /sync/{id}`.

---

## Multi-tenancy — read this before adding an endpoint

Every row this service owns belongs to exactly one FinPilot company.

- The company is resolved **once**, by `get_company_id` (`app/core/tenancy.py`), from the
  `X-Company-ID` header, falling back to `DEFAULT_COMPANY_ID`.
- Endpoints receive an `Installation` via `Depends(get_installation_for_company)`. They must
  **never** accept a client-supplied `installation_id` or `company_id`.
- Single-file endpoints resolve the file through `_get_file_scoped(db, file_id, installation.id)`,
  which filters on `installation_id` so another company's `file_id` returns 404 instead of data.

> **Interim state:** the `X-Company-ID` header is *not* cryptographically verified, because
> FinPilot has no Auth Service / API Gateway yet. Once a gateway issues JWTs, replace
> `get_company_id` with claim extraction from the verified token and drop the
> `DEFAULT_COMPANY_ID` fallback. Until then this service must not be exposed publicly.

Secrets never leave the service: bot tokens are Fernet-encrypted (`TokenCipher`) before they
touch the database, logs run through a redacting formatter, and no response body or redirect
URL contains a token or an S3 key.

---

## Layout

```
app/
├── main.py                  FastAPI app, CORS, router mounting, /health
├── worker.py                Celery app + run_sync task
├── api/routes/
│   ├── auth.py              connect, callback, status, disconnect
│   └── sync.py              sync jobs, file list/detail, category, retry, send-to-scanner
├── core/
│   ├── config.py            Settings (env-driven, ALL-CAPS aliases)
│   ├── security.py          TokenCipher — Fernet encryption + OAuth state signing
│   ├── tenancy.py           company resolution + company-scoped installation lookup
│   └── logging.py           structured logging with secret redaction
├── db/session.py            async engine + get_db dependency
├── models/                  SQLAlchemy tables
├── schemas/sync.py          Pydantic request/response models
└── services/
    ├── slack/               Slack Web API client, discovery, OAuth helpers
    ├── categorization/      rule-based classifier + rules.yaml
    ├── download_manager.py  S3/MinIO storage
    ├── reconciliation.py    audit + FileRetryManager
    ├── sync_orchestrator.py ties discovery → categorize → download together
    └── scanner_bridge.py    hands a file to the Invoice Service
```

Differences from the generic service shape in
`FinPilot_AI_Backend_Architecture_Report`: this service has no `repositories/` layer (query
code lives in the services that use it) and uses Celery + Redis rather than a RabbitMQ
`events/` module. Both are inherited from the standalone prototype this was ported from and
are worth revisiting when a second service exists to share patterns with.

---

## Running it

### With Docker (recommended — brings up Postgres, Redis and MinIO too)

```bash
cd backend/infra
docker compose up --build -d
```

Then check it:

```bash
curl http://localhost:8010/health
```

### Locally

Requires Python 3.11+ and a reachable Postgres, Redis and S3/MinIO.

```bash
cd backend/services/slack-connector
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Linux/macOS: .venv/bin/pip
cp .env.example .env                          # then fill in real values
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn app.main:app --port 8010 --reload
```

The Celery worker is a separate process — sync jobs stay queued forever without it:

```bash
.venv/Scripts/celery -A app.worker.celery_app worker --loglevel=INFO --pool=solo
```

### Configuration

Copy `.env.example` and fill it in. `.env` is gitignored and must never be committed.

| Variable | Notes |
| --- | --- |
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` / `SLACK_SIGNING_SECRET` | From your Slack app at api.slack.com/apps |
| `SLACK_REDIRECT_URI` | Must match the Slack app exactly: `http://localhost:8010/api/v1/slack/callback` |
| `TOKEN_ENCRYPTION_KEY` | Fernet key — generate with the command below. **Rotating it makes stored tokens undecryptable.** |
| `DATABASE_URL` | `postgresql+asyncpg://…` |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis DB indices 3 / 4 / 5 (0–2 are reserved for Gateway, Auth and AI Engine) |
| `S3_*` | MinIO locally; `S3_PUBLIC_ENDPOINT_URL` is what signed URLs are handed to the browser as |
| `DEFAULT_COMPANY_ID` | Interim tenancy fallback when no `X-Company-ID` header is sent |
| `INVOICE_SERVICE_URL` | Where send-to-scanner POSTs |

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Endpoints

Interactive docs at `http://localhost:8010/docs`. Full contract in
[`backend/docs/api-contracts.md`](../../docs/api-contracts.md).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness — no auth, no DB |
| `GET` | `/api/v1/slack/connect` | Starts OAuth; 302 to Slack |
| `GET` | `/api/v1/slack/callback` | OAuth return; stores installation, redirects to frontend |
| `GET` | `/api/v1/slack/status` | Connection status for this company |
| `DELETE` | `/api/v1/slack/installation` | Disconnect + drop stored token |
| `POST` | `/api/v1/slack/sync/` | Queue a sync; returns `sync_job_id` |
| `GET` | `/api/v1/slack/sync/{sync_id}` | Sync progress/result |
| `GET` | `/api/v1/slack/files/` | Paginated file list (`skip`, `limit`, `category`) |
| `GET` | `/api/v1/slack/files/{file_id}` | One file's metadata |
| `GET` | `/api/v1/slack/files/{file_id}/preview` | Short-lived signed S3 URL |
| `GET` | `/api/v1/slack/files/{file_id}/content` | Streams bytes through the service |
| `PATCH` | `/api/v1/slack/files/{file_id}/category` | Manual re-categorise (`category_source` → `manual_override`) |
| `POST` | `/api/v1/slack/files/{file_id}/retry` | Re-queue a failed download |
| `POST` | `/api/v1/slack/files/{file_id}/send-to-scanner` | Push the file to the Invoice Service |

---

## Tests

```bash
cd backend/services/slack-connector
.venv/Scripts/pytest tests/ -v
```

24 unit tests covering token encryption, OAuth state round-trip and tamper rejection,
company scoping, the categorisation rules, file retry, and the scanner bridge. They use an
in-memory SQLite database, so no Postgres is needed.

Note: `installation.scopes` and `sync_job.errors` are declared as
`ARRAY(String).with_variant(JSON(), "sqlite")` — Postgres gets a real array (matching the
Alembic migrations) while SQLite, which cannot compile `postgresql.ARRAY`, gets JSON for tests.

## Migrations

```bash
.venv/Scripts/alembic upgrade head              # apply
.venv/Scripts/alembic upgrade head --sql        # print DDL without touching a database
.venv/Scripts/alembic revision -m "what changed"   # new revision
```

`20260818_04_add_company_id` is the FinPilot-specific one: it adds `installation.company_id`,
backfills existing rows with generated UUIDs, then makes it `NOT NULL UNIQUE`.
