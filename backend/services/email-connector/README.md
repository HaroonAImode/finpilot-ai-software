# Email Connector Service

Connects a FinPilot company's Gmail mailbox, so invoices and receipts arriving as email
attachments flow into FinPilot without anyone downloading and re-uploading them by hand.

**Port:** `8011` · **Prefix:** `/api/v1/email` · **Database:** `email_connector_db`

**Status: Phase 1, OAuth foundation only.** Connect/status/disconnect are built and
live-tested. Message sync, attachment discovery, and the AI Scanner hand-off are the next
slice — see `docs/email-connector-plan.md` for the full phased plan and rationale.

---

## How it works today

```
Google OAuth (gmail.readonly, openid, email)
        │
        ▼
EmailAccount (1 per company, both tokens encrypted at rest)
        │
        ▼
GET /status ── whether a mailbox is connected, and to whom
DELETE /account ── hard-deletes the row (see below — this differs from Slack)
```

Everything here is deliberately the same shape as `backend/services/slack-connector`:
same `company_id` tenancy pattern, same Fernet token encryption, same OAuth
state-in-a-signed-cookie CSRF protection. If you understand that service, you understand
this one.

## What's different from the Slack connector, and why

- **Access tokens expire.** Slack's bot token never does. Gmail's does (~1 hour), so every
  `EmailAccount` also stores a `refresh_token_encrypted` and `token_expires_at`. The
  authorization URL always sends `access_type=offline&prompt=consent` so Google is forced
  to return a refresh token every time — without `prompt=consent`, Google only issues one
  on a user's very first-ever authorization, and a reconnect after revoking access would
  silently get none. The callback refuses to store an account with no refresh token rather
  than let it die silently in an hour.
- **Disconnecting hard-deletes the row**, not a status flag. Slack's `DELETE /installation`
  just flips `status` to `revoked`. A mailbox holds personal, financial and legal
  correspondence in one place — more sensitive than a Slack workspace — so
  `docs/email-connector-plan.md` §9 calls for deletion to actually delete. Once
  `EmailMessage`/`EmailAttachment` exist (next phase), their foreign keys should cascade
  from `email_account` so a disconnect also removes anything already downloaded.
- **Provider abstraction.** `app/services/gmail/oauth.py` holds every Gmail-specific call.
  A `MicrosoftProvider` (Phase 5) implementing the same shape — `authorization_url`,
  `exchange_code`, `refresh_access_token` — is meant to be addable without touching routes,
  models, or tenancy.

## Multi-tenancy

Every row belongs to exactly one company. `get_company_id` (`app/core/tenancy.py`) resolves
it from a verified JWT first — the only trustworthy source — falling back to an unverified
`X-Company-ID` header only when `TRUST_COMPANY_HEADER=true`, which the service refuses to
allow when `APP_ENV=production`. Identical convention to the Slack connector.

## Local development

```bash
cp .env.example .env   # then fill in a real Google OAuth client — see the file's comments
python -m venv .venv && .venv/Scripts/pip install -e ../../libs/shared -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8011
```

Running the test suite needs no real Google credentials — every OAuth call is mocked at
the `httpx.AsyncClient` boundary (`tests/unit/test_gmail_oauth.py`,
`tests/unit/test_auth_routes.py`).

```bash
.venv/Scripts/pytest -q
```
