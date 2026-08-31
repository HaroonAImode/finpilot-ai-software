# Email Connector — Build Plan & Research Guide

**Status:** ✅ Gmail is done — connected, synced, and live-verified against a real mailbox (Phases
1-4 complete; see §3). **Outlook (Phase 5) is on hold, blocked on the account owner's side, not a
coding gap**: the code, tests, and even the frontend "Connect Outlook" button are all built and unit
tested (see §11a) — what's missing is a real Azure AD App Registration to test against. That's
blocked for two independent reasons the account owner ran into directly: (1) Azure account/app
creation was refused after repeated attempts ("account creation blocked"), and (2) the university
email available otherwise does not have permission to register new Azure AD applications on its
tenant. Neither is something this codebase can work around — there is no way to obtain Microsoft
Graph API access without an app registration belonging to *someone's* Azure identity. **Picking this
back up later just means: get a usable Azure account (a personal Microsoft account not subject to
either restriction above works fine — see §11a's setup steps), register the app, drop the client
id/secret into `.env`, and everything downstream already works.** Auth Service exists (see §11
below, resolved), so this connector reads `company_id` from a verified JWT from day one.
**Target:** `backend/services/email-connector`, port **8011**.
**Sequence decision:** building **Gmail OAuth (Option A) directly as Phase 1**, not the forwarding
address (Option D) this doc originally recommended going first. Rationale: Option D is the faster,
lower-risk *demo* path, but Gmail OAuth is the real product surface and the one users actually asked
for — so the recency of that decision belongs here: Option D is deferred to "later, if ever," not
dropped as a concept, since it stays useful as a fallback if Google's verification review turns out
to be slow. See §3 for the phase breakdown.

---

## 1. What this service is for

Most Pakistani SME invoices arrive as **email attachments**: a vendor emails a PDF invoice, an
airline emails a receipt, a supplier emails a statement. Today someone opens the mail, downloads
the file, and re-uploads it into the scanner by hand.

This service removes that step: connect a mailbox once, and matching attachments flow into
FinPilot automatically, categorised, ready to send to the AI Scanner.

**It is deliberately the same shape as the Slack connector.** Everything in §5.11 of the
architecture report applies: same endpoint contract, same `company_id` scoping, same encrypted
token storage, same Celery worker pattern. If you understand `backend/services/slack-connector`,
you understand 80% of this service before writing a line.

---

## 2. The decision that matters most: how to connect to email

This is the fork in the road. **Research this before writing code** — it changes auth, scopes,
sync strategy and cost.

### Option A — Gmail API (OAuth) ✅ recommended starting point

| | |
|---|---|
| **How** | Google OAuth, `gmail.readonly` scope, Gmail REST API |
| **Good** | Proper OAuth (no passwords), push notifications via Pub/Sub, powerful search (`has:attachment`), per-message IDs make dedupe easy |
| **Bad** | Google verification review needed for production (can take weeks), Gmail only |
| **Cost** | Free within generous quotas |

**Why start here:** most SMEs and freelancers in Pakistan use Gmail or Google Workspace. The OAuth
flow is nearly identical to the Slack one you already have, so the code shape is familiar.

⚠️ **Research task for you:** Google requires an app verification / security assessment for
*restricted* scopes. `gmail.readonly` is restricted. Find out the current requirements and
timeline — this is the single biggest schedule risk in this service, and it is a paperwork risk,
not a coding one. Test mode allows up to 100 test users without verification, which is enough for
your team to trial it.

### Option B — Microsoft Graph (Outlook / Office 365)

Same architecture as Gmail, different provider. `Mail.Read` scope. Worth adding **second**, since
many businesses run Microsoft 365. Design the service so the provider is pluggable and this is an
additional adapter, not a rewrite.

### Option C — IMAP (generic, any mailbox)

| | |
|---|---|
| **Good** | Works with literally any mail host, no vendor review, no OAuth |
| **Bad** | **Requires storing the user's email password** (or an app-specific password) |

⚠️ **Security judgement:** storing a customer's actual mailbox password is a materially different
risk from storing an OAuth token. An OAuth token is scoped (read-only, revocable, cannot send
mail); a password is total account access. If you support IMAP, prefer providers that offer
app-specific passwords, encrypt at rest with the same Fernet approach, and be explicit in the UI
about what is stored. **Do not make IMAP the default.**

### Option D — Forwarding address (no mailbox access at all)

Give each company a unique address (`docs+<company>@inbox.finpilot.app`); the user forwards or
auto-forwards invoices to it.

| | |
|---|---|
| **Good** | Zero OAuth, zero verification review, zero password storage, trivially simple, no privacy concerns |
| **Bad** | Requires a user habit; not automatic for existing mail |

⚠️ **Strong recommendation:** this is the **fastest path to a working demo** and the one with the
lowest security surface. Consider shipping D first (a week of work) while the Gmail verification
paperwork is in flight, then adding A. Services like Postmark, Mailgun, or SES inbound can POST
parsed inbound mail to a webhook.

---

## 3. Build sequence (as of the current phase)

```
Phase 1  ✅ Gmail OAuth, encrypted token storage, account connection — built and
         live-verified with a real Google account (see CHANGELOG 2026-08-20).
         Email sync + attachment discovery/download are the one part of
         "Phase 1" as originally scoped that slid into Phase 2 below —
         connecting the account turned out to be worth shipping and testing
         on its own before building sync on top of it.
Phase 2  ✅ Built and live-verified (57 real attachments imported). Message
         sync via historyId + bounded backfill, attachment discovery/
         download to MinIO, size/extension filtering, categorisation,
         email/source metadata, duplicate handling, sender allow/deny lists
         + the "needs review" tray (§5b), and the send-to-scanner hand-off
         to Invoice Service.
Phase 3  ✅ Built and live. Connected Apps card (connect/status/disconnect),
         sync status polling, "Sync now", Documents page listing synced
         attachments, preview (including .docx via client-side conversion),
         retry-download on a failed attachment.
Phase 4  ✅ Mostly built and live-verified: scheduled incremental sync
         (Celery Beat, SCHEDULED_SYNC_MINUTES), token refresh/revocation
         handling, retry/error recovery, and a stuck-job reconciliation task
         — the last one caught and fixed two genuinely stuck jobs left over
         from earlier manual testing, not a contrived scenario.
         🚧 Deliberately not built: Gmail push notifications. A real
         implementation needs a public HTTPS endpoint Google's Pub/Sub can
         POST to, plus a `watch` subscription renewed every ~7 days — this
         dev environment has no public ingress at all (no ngrok/tunnel, no
         deployed public URL), the same reasoning §6 already gives for not
         building Slack event subscriptions. Scheduled polling (above)
         covers the same need at a coarser interval and was built instead;
         push notifications are worth revisiting once there is a real
         public deployment to point Pub/Sub at.
Phase 5  ✅ Code built: Microsoft Graph (Outlook/365) as a second provider
         through a MailProviderClient abstraction
         (app/services/providers/base.py) — SyncOrchestrator, the sync
         engine's single most bug-prone file (§5a's four live-found bugs all
         lived there), now drives either provider through one normalized
         contract rather than a duplicated engine. OAuth, message listing
         (delta query), message/attachment normalization, and the connect UI
         (a second "Connect Outlook" button) are all in place and unit
         tested against mocked Graph responses (181 backend tests passing).
         ⏸️ ON HOLD — not a coding gap. Live-verifying this needs a real
         Azure AD App Registration, and the account owner hit two separate
         real-world walls trying to create one: personal Microsoft account
         creation was refused after repeated attempts, and the university
         email account otherwise available is not permitted to register new
         Azure AD applications on its tenant. This has nothing to do with
         the code — GET /connect?provider=outlook already returns a clean
         503 rather than a broken flow (confirmed live), and everything
         downstream of "paste a client id/secret into .env" already works.
         Picking this back up later is a paperwork/account task, not a
         development task — see §11a for the exact setup steps whenever a
         usable Azure identity is available.
```

Start the **Gmail verification paperwork now, in parallel** — it is a paperwork risk, not a coding
one, and the single biggest schedule risk in this service (see §12.1). The forwarding-address
option (D) and IMAP (C) are not part of this sequence; they remain documented above as fallbacks,
not upcoming phases.

---

## 4. Data model

Mirrors the Slack connector, with mail-specific fields. Reuse the same
`company_id`-on-every-table rule.

```
EmailAccount            ← the Slack "Installation" equivalent
  id, company_id (unique), provider ('gmail'|'outlook'|'imap'|'forwarding'),
  email_address, access_token_encrypted, refresh_token_encrypted,
  token_expires_at, scopes, status ('active'|'revoked'|'needs_reauth'),
  connected_at, last_synced_at

EmailMessage            ← the "Conversation" equivalent
  id, account_id, provider_message_id (unique per account),
  subject, from_address, from_name, to_address,
  received_at, has_attachments, snippet, raw_headers_json

EmailAttachment         ← the "File" equivalent; same columns as Slack's File
  id, account_id, message_id, provider_attachment_id,
  filename, mimetype, size, sha256_hash, s3_key,
  category, category_confidence, category_source,
  downloaded, download_failed, download_error,
  created_at

EmailSyncJob            ← same as SyncJob
  id, account_id, status, started_at, completed_at,
  messages_scanned, attachments_discovered, attachments_downloaded,
  attachments_failed, errors
```

### Learn from the Slack bugs

Three bugs were found only by running a real Slack sync. **Do not repeat them:**

1. **Increment the job counters where the work happens.** `attachments_downloaded` must be bumped
   in the download function itself, with the job object passed in.
2. **Upsert, never blind-insert.** Look up by `(account_id, provider_attachment_id)` and update.
   A second sync must not duplicate rows. And never overwrite a category a human set to
   `manual_override`.
3. **Wrap list columns in `MutableList`.** `errors.append(...)` on a plain `ARRAY` column is
   silently discarded by SQLAlchemy. Use
   `MutableList.as_mutable(ARRAY(String).with_variant(JSON(), "sqlite"))`.

Write the test for each of these *first* — each one cost real debugging time on Slack.

---

## 5. The filtering problem (this is the actual hard part)

Slack was easy: sync every file in every accessible channel. **Email is not** — a mailbox has
thousands of messages and most attachments are not financial documents (signatures, logos,
newsletters, memes).

Sync everything and you will fill object storage with email signature images and give the user a
Documents page full of noise.

**Layered filter, cheapest checks first:**

```
1. Provider-side query      has:attachment, and a date floor (e.g. newer_than:1y)
                            — let Gmail do the work; never page the whole mailbox
2. Attachment size          skip < 20 KB (signature images, tracking pixels)
3. MIME / extension         allow pdf, jpg, png, xlsx, csv, docx
                            deny  ics, vcf, p7s, asc, gif
4. Filename heuristics      boost: invoice, receipt, bill, statement, FBR, GST, tax
                            drop:  logo, signature, image00x, unnamed
5. Sender rules             a per-company allow/deny list, learned over time
6. Existing categorisation  reuse app/services/categorization/rules.yaml as-is
```

⚠️ **Design the sender rules as a first-class feature, not an afterthought.** In practice the
biggest quality win is "always import from `billing@vendor.com`, never from `newsletter@`". Expose
it in the UI. This is the difference between a feature people trust and one they turn off.

**Suggested default:** import only attachments that score above the categorisation confidence
threshold *or* come from an allow-listed sender — and show everything else in a "Needs review"
tray the user can approve from. Silent over-collection is worse than asking.

## 5b. Sender rules + needs-review tray as built

Motivated by the first real sync's numbers: 57 attachments imported, **34 of them images** —
mostly signatures and inline graphics, exactly the noise §5 predicted.

**Decision flow**, evaluated per attachment after the size/extension filter:

```
sender matches a DENY rule    → skipped entirely, never stored, never fetched
sender matches an ALLOW rule  → imported (downloaded immediately)
category confidence ≥ 0.70    → imported
otherwise                     → needs_review
```

`SenderRule` holds one pattern per row, scoped to the account. A pattern is either a full
address (`billing@vendor.com`) or a bare domain (`vendor.com`), which matches any address
at that domain. Deny beats allow when both match, because the safe failure for a rule
conflict is to collect less, not more.

**A needs-review attachment is metadata only — its bytes are never downloaded.** This is
the whole point: if the user never approves it, FinPilot never holds a copy. Approving
re-fetches from Gmail on demand. That costs one extra API call per approval and is the
reason `review_status` had to exist as a real state rather than a display filter over
already-downloaded files.

⚠️ **Re-fetching needs a fresh `attachmentId`** — the stored one is expired (see §5a.1). So
approval re-fetches the *message* first to get a live id, then downloads. The stored
`attachment_index` is what locates the right part in that re-fetched message; without a
stable index this flow would be impossible, which is the second reason that column exists.

```
GET    /api/v1/email/sender-rules/          list
POST   /api/v1/email/sender-rules/          { pattern, action }
DELETE /api/v1/email/sender-rules/{id}
GET    /api/v1/email/files/?review_status=needs_review
POST   /api/v1/email/files/{id}/approve     downloads it, flips to imported
POST   /api/v1/email/files/{id}/reject      flips to rejected, stays un-downloaded
```

Approving or rejecting from a sender also offers to create the matching rule, which is how
the allow/deny lists actually get populated in practice — nobody sits down to write them
up front.

---

## 5a. Gmail API facts learned the hard way

All four found by running against a real mailbox, not by reading docs — recording them
here because each cost a full build-and-retry cycle and none is obvious from the API
reference.

1. **`attachmentId` is ephemeral.** Gmail mints a fresh one on every `messages.get` for
   the same unchanged attachment. It is a short-lived fetch handle, *not* an identity.
   Keying dedup on it means every re-sync inserts a complete duplicate set — observed:
   114 rows for 57 real attachments, one file with 12 distinct ids across 12 runs.
   **Dedup on the attachment's position in the message's MIME tree instead**; a received
   message is immutable, so position is stable.
2. **`attachmentId` is also huge** — routinely 300-400+ characters. It does not fit a
   `VARCHAR(256)`, and it cannot be used as an S3/MinIO key segment (MinIO rejects any
   `/`-separated segment over ~255 bytes with `XMinioInvalidObjectName`). Use an
   internally-owned id for storage keys.
3. **`history.list` 404s on a cursor older than ~30 days.** That is not "nothing new" —
   it means the place was lost, and the only recovery is a bounded backfill. Silently
   treating it as an empty result would look like a working sync that imports nothing.
4. **Access tokens expire in ~1 hour**, which is shorter than a real backfill takes. Token
   refresh is not a "later, in production" concern the way it would be for Slack — a sync
   cannot complete without it.

## 6. Sync strategy

| Mode | When | How |
|---|---|---|
| **Backfill** | Once, at connect time | Walk history back N months (ask the user: 3 / 6 / 12), page through, respect rate limits |
| **Incremental** | Every N minutes | Gmail: `historyId` since last sync. Outlook: delta query. Never re-scan the whole mailbox |
| **Push** | Ideal end state | Gmail Pub/Sub watch → webhook. Near-real-time, far fewer API calls |

Start with **backfill + polling incremental**; add push later. Same Celery + Redis setup as the
Slack worker.

⚠️ **Gmail `watch` subscriptions expire after 7 days** and must be renewed — a scheduled Celery
beat task. Easy to forget and it fails silently.

---

## 7. Endpoints (identical contract to Slack)

```
GET    /api/v1/email/connect                    → 302 to provider consent
GET    /api/v1/email/callback                   → completes OAuth, stores encrypted tokens
GET    /api/v1/email/status                     → { connected, email_address, provider, ... }
DELETE /api/v1/email/account                    → revoke + forget
POST   /api/v1/email/sync/                      → queue a sync
GET    /api/v1/email/sync/{id}                  → progress
GET    /api/v1/email/files/                     → paginated attachments (the Documents page calls this)
GET    /api/v1/email/files/{id}/content         → stream bytes, Content-Disposition: inline
PATCH  /api/v1/email/files/{id}/category        → human correction
POST   /api/v1/email/files/{id}/send-to-scanner → hand to Invoice Service
```

Keeping `/files/` as the noun (rather than `/attachments/`) means the frontend adapter is a
near-copy of the Slack one.

### Frontend work is genuinely small

Because `frontend/src/lib/documents.ts` already abstracts sources, the entire frontend change is:

1. Add `listEmailFiles` etc. to a new `src/lib/email-connector.ts` (copy the Slack client)
2. Add an `emailFileToDocument` mapper
3. Change the `email` entry in `DOCUMENT_SOURCES` from `status: "planned"` to `"available"` and
   attach the adapters

The Documents page, preview dialog, and bulk actions all work unchanged. That was the point of
building the registry.

---

## 8. Token refresh — the one thing Slack didn't teach you

Slack bot tokens do not expire. **OAuth access tokens for Gmail and Microsoft do** (typically 1
hour), and you must use the refresh token to get a new one.

This is new logic with no Slack equivalent:

- Store `refresh_token_encrypted` and `token_expires_at`
- Before any API call, if the token expires within ~5 minutes, refresh it first
- Persist the new access token
- If refresh fails (user revoked access, password changed), set status `needs_reauth` and surface
  a **Reconnect** button in the UI — do not fail silently
- Refresh tokens can be revoked at any time by the user from their Google account

⚠️ Get this wrong and the connector works for an hour then mysteriously stops. Write the
"expired token triggers refresh" test before the happy path.

---

## 9. Privacy and security — take this seriously

A mailbox is far more sensitive than a Slack channel. It contains personal correspondence,
medical, legal and banking mail.

- **Request the narrowest scope that works.** `gmail.readonly` reads everything; check whether
  `gmail.metadata` plus per-message fetch is sufficient for your flow.
- **Store only what you need.** You need the attachment and minimal message context. You do not
  need to store full email bodies — do not, unless there is a concrete reason.
- **Never log message contents, subjects, or tokens.** Extend the existing
  `SecretRedactingFormatter`.
- **Be explicit in the UI** about what FinPilot reads and stores, before the consent screen.
- **Deletion must actually delete** — disconnecting should remove stored attachments and tokens,
  not just flip a flag.
- Same rule as Slack: **every query scoped by `company_id`**, cross-tenant access returns a
  generic 404.

---

## 10. Realistic effort estimate

| Phase | Work | Estimate |
|---|---|---|
| Forwarding address (D) | inbound webhook, parsing, storage, UI | 3–5 days |
| Gmail OAuth (A) | OAuth, refresh, backfill, incremental, filtering | 2–3 weeks |
| Filtering quality | rules, sender lists, needs-review tray | 1 week, ongoing |
| Microsoft Graph (B) | second provider adapter | 1 week |
| Google verification | paperwork, security questionnaire | **weeks — start early, runs in parallel** |

---

## 11. Build this after auth? — resolved

Auth Service is built and live (`backend/services/auth`, JWT-based, verified in the Gateway and
independently in each service via `shared.auth.decode_access_token`). Email Connector's
`core/tenancy.py` reads `company_id` from a verified JWT the same way Slack's does — the
`X-Company-ID` dev-header fallback exists only for local development without a token and is
refused outright when `APP_ENV=production` (identical guard to Slack's `TRUST_COMPANY_HEADER`).
No header-only shim ships for email at any point.

## 11a. Provider abstraction — built (Phase 5)

Built as a `MailProviderClient` Protocol (`app/services/providers/base.py`), narrower in scope than
originally sketched here: `get_profile`, `list_message_ids_since(cursor)`,
`list_message_ids_backfill(window_days)`, `get_message(id) -> NormalizedMessage`,
`get_attachment_bytes(message_id, attachment)`. `GmailProviderClient`
(`app/services/gmail/provider.py`) adapts the existing raw `GmailClient` to this shape;
`OutlookProviderClient` (`app/services/outlook/client.py`) implements it directly against Microsoft
Graph, since Graph's JSON is already close to flat and gains nothing from a separate raw layer.
`SyncOrchestrator` — the sync engine's single most bug-prone file, per §5a's four live-found bugs —
is written entirely against this contract and does not know which provider it is talking to.

**Deliberately not abstracted**: OAuth (`connect`/`callback` routes), on-demand re-fetch
(`attachment_fetch.py`), and token refresh (`providers/token_manager.py`) each branch once on
`account.provider` and call the right provider's functions directly, rather than going through the
Protocol. Each branch is a handful of lines; forcing them through a shared interface would add
indirection without removing any real duplication — the Protocol exists specifically for
`SyncOrchestrator`, where duplicating the logic instead would mean duplicating a state machine with
a real history of production bugs.

**One genuine protocol difference the abstraction has to absorb**: Gmail's `historyId` is a
watermark captured *before* listing (so mail arriving mid-sync is caught by the *next* run, never
missed); Microsoft Graph's delta query is edge-consistent by construction, so its `@odata.deltaLink`
is only meaningful *after* a full page walk completes. `MailProviderClient.next_cursor` is set by
each implementation at the point that is actually safe for its own protocol — see the docstring on
that attribute for the full reasoning.

**Setup steps for whenever this is picked back up** (needs a Microsoft account that isn't blocked
from creating an Azure AD App Registration — a personal Microsoft account not subject to either
restriction hit so far, e.g. a fresh outlook.com/hotmail.com signup, is the simplest path):

1. **https://portal.azure.com** → search **"App registrations"** → **New registration**.
2. Name it anything. **Supported account types**: must be *"Accounts in any organizational
   directory (Any Microsoft Entra ID tenant – Multitenant) and personal Microsoft accounts"* — the
   code authorizes against Microsoft's `common` endpoint, which only works with this option (a
   narrower choice fails login with `AADSTS50020`).
3. **Redirect URI**: platform **Web**, URI `http://localhost:8011/api/v1/email/callback` (must match
   `MICROSOFT_REDIRECT_URI` in `.env` exactly).
4. Copy the **Application (client) ID** from the Overview page → `MICROSOFT_CLIENT_ID`.
5. **Certificates & secrets** → **New client secret** → copy the secret's **Value** immediately (only
   shown once) → `MICROSOFT_CLIENT_SECRET`.
6. **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated** → add `Mail.Read`,
   `offline_access`, `openid`, `email`. No admin consent needed — these are all user-consentable.
7. Put all three values in `backend/services/email-connector/.env`, restart the `email-connector`
   container, then either click "Connect Outlook" in Settings or hit `/connect?provider=outlook`
   directly to confirm it now returns a real `login.microsoftonline.com` URL instead of a 503.

## 11b. Correction against the wider architecture report

The architecture report's Invoice Service / AI Engine / RabbitMQ sections (§5.3, §5.8, and the
event-flow diagrams) describe the **target** system, not what is built today. As of this phase,
none of `rabbitmq`, `invoice-service`, or `ai-engine` exist in `docker-compose.yml` — the only
running services are `gateway`, `auth`, and `slack-connector`. Slack's own hand-off
(`services/scanner_bridge.py`) already accounts for this: it POSTs directly to
`INVOICE_SERVICE_URL` over plain HTTP and degrades to a clear "Invoice Service isn't running yet"
error rather than assuming a queue exists. Email Connector's hand-off copies that same bridge
pattern verbatim. This is not a scope reduction — it is building against what actually runs today,
per the same principle used throughout this project (source of truth is the code, not the target
diagram). When RabbitMQ/Invoice Service/AI Engine are eventually built, both connectors' bridges
upgrade together in one pass, rather than Email inventing a second, inconsistent integration today.

---

## 12. What to research before writing code

1. **Google OAuth verification** for `gmail.readonly` — current requirements, timeline, and
   whether test mode (100 users) covers your trial. *Biggest schedule risk.*
2. **Inbound email providers** — Postmark vs Mailgun vs SES inbound: pricing, parsed-webhook
   format, attachment size limits.
3. **Gmail API quotas** — units per method, daily limits, how backfill of a large mailbox behaves.
4. **Gmail push (Pub/Sub)** — setup cost and the 7-day `watch` renewal.
5. **Microsoft Graph delta queries** — how they differ from Gmail `historyId`.
6. **Real invoice emails from your own inbox** — collect 20–30 and check what the existing
   `rules.yaml` categorisation does with those filenames. This is the cheapest, highest-value
   research on the list and you can do it today.

---

*Related: architecture report §5.11 (connector contract), `backend/services/slack-connector/README.md`
(the reference implementation), `CHANGELOG.md` (bugs found running the Slack one).*
