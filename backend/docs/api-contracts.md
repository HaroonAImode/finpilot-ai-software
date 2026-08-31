# FinPilot AI — Backend API Contracts

Endpoint reference for the FinPilot backend services. Each service owns its own section.

Planned service/port map is in `FinPilot_AI_Backend_Architecture_Report`; only services that
actually exist are documented here.

| Service | Port | Status |
| --- | --- | --- |
| Gateway | 8000 | not built |
| Auth | 8001 | not built |
| Invoice | 8002 | not built |
| Transactions | 8003 | not built |
| HR | 8004 | not built |
| Procurement | 8005 | not built |
| Vendors | 8006 | not built |
| AI Engine | 8007 | not built |
| Reports | 8008 | not built |
| Settings | 8009 | not built |
| **Slack Connector** | **8010** | **built** |

---

## Conventions

**Tenancy.** No service is behind an API Gateway yet, so there is no verified JWT. Requests
carry the FinPilot company in an `X-Company-ID` header; if absent, the service falls back to
its `DEFAULT_COMPANY_ID` env var. This is an explicit interim measure — see the security note
in each service's README. Services must never accept a company or installation id in the
request body or query string.

**Errors.** FastAPI's default shape:

```json
{ "detail": "Human-readable message" }
```

| Code | Meaning here |
| --- | --- |
| 400 | Bad input, or a tampered/expired OAuth state |
| 404 | Not found **or not owned by this company** (deliberately indistinguishable) |
| 409 | Conflict — e.g. the Slack workspace belongs to a different company |
| 502 | An upstream dependency (Slack, S3, another service) failed |

---

## Slack Connector — `/api/v1/slack`

Base URL in local dev: `http://localhost:8010`. The frontend calls it through a Vite proxy,
so browser-side paths are relative (`/api/v1/slack/...`).

### `GET /health`

Liveness probe. No auth, no database access.

```json
{ "status": "ok" }
```

### `GET /api/v1/slack/connect`

Begins the OAuth flow. Responds `302` to Slack's `oauth/v2/authorize`.

The `state` parameter is an encrypted, TTL-limited payload of `{nonce, company_id}` — the
company must survive the round-trip because Slack redirects the *browser* back, and without a
session there is nothing else to recover it from. A `nonce` cookie is also set (`httponly`,
`samesite=lax`) and compared on return with a constant-time comparison.

### `GET /api/v1/slack/callback`

Slack's redirect target. Query: `code`, `state`.

Exchanges the code, encrypts the bot token, upserts `workspace` + `installation`, then
redirects to `FRONTEND_BASE_URL`. No token is ever placed in the redirect URL.

| Outcome | Response |
| --- | --- |
| Success | `302` to the frontend with the workspace name |
| Bad/expired state, or nonce mismatch | `400` |
| Workspace already linked to a **different** company | `409` (does not reveal which company) |

### `GET /api/v1/slack/status`

```json
{
  "connected": true,
  "workspace_name": "Acme Workspace",
  "scopes": ["files:read", "channels:read"],
  "installed_at": "2026-08-18T10:00:00Z"
}
```

When not connected: `{ "connected": false }`. Never includes a token.

### `DELETE /api/v1/slack/installation`

Disconnects this company's workspace and deletes the stored encrypted token. Already-synced
file rows are retained. Returns `{ "connected": false }`.

### `POST /api/v1/slack/sync/`

Queues a discovery + download run on the Celery worker. Returns immediately:

```json
{ "sync_job_id": "uuid", "status": "queued", "message": "Sync started" }
```

Requires a connected installation (`404` otherwise). A worker must be running or the job
stays `queued` indefinitely.

### `GET /api/v1/slack/sync/{sync_id}`

```json
{
  "id": "uuid",
  "status": "running",
  "started_at": "2026-08-18T10:00:00Z",
  "completed_at": null,
  "total_conversations": 42,
  "conversations_processed": 17,
  "files_discovered": 128,
  "files_downloaded": 96,
  "files_failed": 2,
  "errors": []
}
```

`status` is one of `queued` · `running` · `completed` · `failed`. Scoped to the caller's
installation, so another company's `sync_id` is a `404`.

### `GET /api/v1/slack/files/`

| Query | Default | Notes |
| --- | --- | --- |
| `skip` | `0` | `>= 0` |
| `limit` | `20` | `1..100` |
| `category` | — | Exact match, e.g. `Invoices` |

```json
{ "files": [ /* FileResponse */ ], "total": 128, "page": 1, "page_size": 20 }
```

**FileResponse**

| Field | Type | Notes |
| --- | --- | --- |
| `id` | uuid | FinPilot's id, not Slack's |
| `slack_file_id` | string | |
| `filename` / `title` | string / string\|null | |
| `file_type` / `mimetype` | string / string\|null | |
| `size` | int | bytes |
| `created_at` | datetime | when shared in Slack |
| `is_external` | bool | |
| `category` | string | see categories below |
| `category_confidence` | float | `0.0–1.0` |
| `category_source` | string | `rule` or `manual_override` |
| `downloaded` / `download_failed` | bool | |
| `shared_by_user_name` | string\|null | |
| `slack_permalink` | string | back-link into Slack |

`s3_key` is deliberately **not** exposed — clients get file bytes via `/preview` or `/content`
so the storage layout stays private.

### `GET /api/v1/slack/files/{file_id}`

One `FileResponse`. `404` if the file belongs to another company.

### `GET /api/v1/slack/files/{file_id}/preview`

A short-lived signed S3 URL, suitable for an `<img>` or `<iframe>`.

```json
{ "url": "https://…?X-Amz-Signature=…", "filename": "invoice.pdf", "mimetype": "application/pdf" }
```

### `GET /api/v1/slack/files/{file_id}/content`

Streams the bytes through the service (`Content-Disposition` attachment) for callers that
should not talk to S3 directly. `502` if object storage fails.

### `PATCH /api/v1/slack/files/{file_id}/category`

```json
{ "category": "Invoices" }
```

Returns the updated `FileResponse` with `category_source` set to `manual_override`, which
stops later reconciliation runs from overwriting the human's choice.

### `POST /api/v1/slack/files/{file_id}/retry`

Clears the failure flags and re-queues the download. Returns the updated `FileResponse`.

### `POST /api/v1/slack/files/{file_id}/send-to-scanner`

Fetches the stored bytes and POSTs them as multipart to
`{INVOICE_SERVICE_URL}/api/v1/invoices/scan` with an `X-Company-ID` header. No Slack token or
S3 credential is forwarded.

| Outcome | Response |
| --- | --- |
| Success | Invoice Service's JSON, passed through |
| File not downloaded yet | `409` |
| Invoice Service rejected/unreachable | `502` |

> The Invoice Service does not exist yet, so this endpoint will `502` until it is built. The
> response shape is whatever that service eventually returns.

### Categories

Assigned by `app/services/categorization/rules.yaml`:

`Invoices` · `Receipts` · `Contracts` · `Project Plans` · `Reports` · `Presentations` ·
`Spreadsheets & Financial` · `Images & Screenshots` · `Bookings & Reservations` · `Archives` ·
`Audio & Video` · `uncategorized`

Only `Invoices`, `Receipts` and `Spreadsheets & Financial` are offered a send-to-scanner
action in the UI.
