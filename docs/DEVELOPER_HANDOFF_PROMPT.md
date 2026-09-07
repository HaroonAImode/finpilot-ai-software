# FinPilot AI Developer Handoff Prompt

> Share this file with the developer or coding agent taking over the repository. Do not commit real `.env` files, OAuth credentials, passwords, tokens, or private receipt data.

## Role and objective

You are taking over the FinPilot AI repository. First bring the local checkout up to date, then configure and run the complete local stack. Preserve the existing architecture and service boundaries. Do not replace the deterministic OCR/extraction pipeline with an LLM or invent a separate ingestion path.

The final validation goal is:

1. Start the Docker Compose stack.
2. Start the frontend.
3. Upload the supplied June 2026 petty-cash receipt fixture through the real Scanner workflow.
4. Confirm the real pipeline uses document preprocessing, LiteParse/PaddleOCR, AI Engine extraction, Invoice Service persistence, category classification, human review, and the `send-to-accounting` hand-off.
5. Confirm reviewed items appear in Saved Records and that categories, totals, dates, vendor/particulars, previews, and category totals behave correctly.
6. Report failures with the service and stage that actually failed. Do not silently change expected values or mark a test as passed because a page loaded.

## Repository and Git history

The first pushed repository commit was:

- `4c3d4c2` — August 31, 2026 — `Initial commit: FinPilot AI platform`

The latest pushed commit is:

- `9480a82` — September 7, 2026 — `docs: add developer setup handoff`

The September 7 push removed `CHANGELOG.md` and `.claude/launch.json` from the repository tree. They are local-only and must not be recreated, staged, or pushed. `.gitignore` is intentionally tracked and must remain tracked.

The initial pushed commit already contains the current backend service directories. Do not assume that a service is missing merely because it was not added in the latest commit. Check the current tree and `backend/infra/docker-compose.yml` before making changes.

### What changed after the first setup

There were no new backend service directories, Docker Compose services, `.env.example`
templates, frontend API clients, or API-contract files added after the August 31 initial
push. The current service stack, including Documents, Vendors, Transactions, HR,
Procurement, Settings, Reports, Slack, Email, AI Engine, and PaddleOCR, was already in
that initial pushed snapshot.

The later pushes added documentation and repository hygiene only:

- Updated the public README and service status descriptions.
- Kept `.gitignore` tracked and added rules for local `.claude/` and `CHANGELOG.md`.
- Removed `CHANGELOG.md` and `.claude/launch.json` from Git tracking.
- Added this handoff document.

Therefore, an existing developer should not recreate Slack/Email connections, regenerate
working secrets, or search for a newly added service solely because of the later pushes.
Pull the latest commit, compare the tracked templates, rebuild the stack, and validate it.

## Safe update procedure

From the repository root:

```powershell
git fetch origin
git status --short --branch
git pull --ff-only origin main
```

If `git pull --ff-only` cannot proceed because of local changes, stop and report the conflict. Do not use `git reset --hard`, delete local work, or overwrite `.env` files without explicit approval.

After pulling, confirm:

```powershell
git status --short --branch
git log -1 --oneline --decorate
git ls-files CHANGELOG.md .claude
```

The last command should print nothing. Local `.env` files should also remain untracked and ignored.

### Existing developer with a working setup

If the developer already cloned the repository, configured every `.env`, created local
databases, and connected Slack or Email, use this shorter path:

```powershell
git fetch origin
git status --short --branch
git diff -- backend/services backend/infra frontend
git pull --ff-only origin main
```

Before starting containers, verify that the existing configuration files still exist:

```powershell
Get-ChildItem backend/services -Recurse -Force -Filter .env | Select-Object FullName
```

Do **not** run the bulk `.env.example` copy command below with `-Force`, and do not replace
existing `.env` files. This preserves the developer's existing JWT secret, Fernet keys,
Slack OAuth settings, Google/Microsoft OAuth settings, redirect URIs, and local defaults.
The `.env.example` files are reference templates, not files to copy over an established
configuration.

Compare templates only when diagnosing a configuration error:

```powershell
git diff 4c3d4c2..origin/main -- '*env.example' '*docker-compose.yml' '*api-contracts.md'
```

For the current repository, that command should show no service, Compose, environment
template, or API-contract change after the initial push. If a future pull does show a
template change, merge the new variable into the existing local `.env` manually, preserve
the developer's existing secret values, and never paste those values into Git, chat, or a
bug report.

Then rebuild and restart the existing local stack so updated application images are used:

```powershell
cd backend/infra
docker compose up --build -d
docker compose ps
```

Do not run `docker compose down -v`; that would delete the developer's local databases,
connector installations, MinIO files, and test state. Do not repeat signup or OAuth
connection setup unless the existing local data was intentionally reset or a health check
shows that the connection is missing.

## Current service map

The backend is a Docker Compose microservice stack. Each service has its own database where applicable.

| Component | Port | Purpose | Public through Gateway? |
| --- | ---: | --- | --- |
| Gateway | 8000 | JWT verification, identity headers, routing | Yes |
| Auth | 8001 | Signup, login, refresh tokens, company scope | Through Gateway |
| Invoice Service | 8002 | Scanner uploads, invoices, categories, Saved Records, sales invoices | Through Gateway |
| Transactions Service | 8003 | Expenses, approvals, dashboard KPIs and trends | Through Gateway |
| HR Service | 8004 | Employees and payroll | Through Gateway |
| Procurement Service | 8005 | Purchase requests, purchase orders and quote comparison | Through Gateway |
| Vendors Service | 8006 | Vendors, spend and reconciliation | Through Gateway |
| AI Engine | 8007 | Deterministic OCR/extraction and validation | Internal service |
| PaddleOCR | 8008 | OCR model server | Internal service |
| Settings Service | 8009 | Company profile, tax settings and automation flags | Through Gateway |
| Slack Connector | 8010 | Slack OAuth, file discovery, sync and preview | Through Gateway |
| Email Connector | 8011 | Gmail/Outlook connector foundation and document sync | Through Gateway |
| WhatsApp Connector | 8012 | Not built; reserved for future work | No |
| Documents Service | 8013 | Browser-uploaded document library | Through Gateway |
| Reports Service | 8014 | P&L, cash flow, tax, sales and purchase reports | Through Gateway |

The local Docker stack also runs PostgreSQL databases, Redis, MinIO, connector workers, and MinIO bucket initialization. RabbitMQ is not required; current internal hand-offs use plain HTTP.

## Prerequisites

Install and verify:

- Docker Desktop with Docker Compose and enough CPU/RAM for PostgreSQL, Redis, MinIO, PaddleOCR, and all FastAPI containers.
- Bun for the frontend. Use Bun rather than npm so no conflicting `package-lock.json` is created.
- Python 3.11+ only if running backend tests or the golden evaluation harness outside Docker.
- PowerShell on Windows, or equivalent shell commands on Linux/macOS.

## Environment setup

Real `.env` files are intentionally not stored in Git. Tracked `.env.example` templates exist for every backend service:

```text
backend/services/ai-engine/.env.example
backend/services/auth/.env.example
backend/services/documents-service/.env.example
backend/services/email-connector/.env.example
backend/services/gateway/.env.example
backend/services/hr-service/.env.example
backend/services/invoice-service/.env.example
backend/services/paddleocr/.env.example
backend/services/procurement-service/.env.example
backend/services/reports-service/.env.example
backend/services/settings-service/.env.example
backend/services/slack-connector/.env.example
backend/services/transactions-service/.env.example
backend/services/vendors-service/.env.example
```

Create a local `.env` beside every `.env.example` without overwriting an existing one:

```powershell
Get-ChildItem backend/services -Recurse -Filter .env.example | ForEach-Object {
  $target = Join-Path $_.DirectoryName '.env'
  if (-not (Test-Path $target)) {
    Copy-Item $_.FullName $target
  }
}
```

Then edit the local files as follows:

1. Generate one strong `JWT_SECRET_KEY` and use the exact same value in Auth, Gateway, and every service template that verifies tokens.
2. Generate a Fernet `TOKEN_ENCRYPTION_KEY` separately for Slack and Email if those connectors are being tested:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. Keep the local development database, Redis, and MinIO values aligned with the `.env.example` templates and `backend/infra/docker-compose.yml`. Compose supplies container-to-container connection overrides.
4. Slack OAuth values are optional for the receipt/OCR test. Only configure them when testing Slack integration.
5. Google OAuth values are optional for the receipt/OCR test. Only configure them when testing Email integration. Never place real OAuth client secrets in this repository or in this handoff file.
6. Keep `APP_ENV=development` for local work. Do not enable the unverified company-header fallback in production.
7. Do not commit any `.env` file. Confirm with `git status --ignored --short` that local secrets are ignored.

The exact variable names and comments in each `.env.example` are authoritative. Do not invent alternate names.

## Start the backend

From the repository root:

```powershell
cd backend/infra
docker compose up --build -d
```

The first build can take a while because the PaddleOCR image contains OCR model dependencies. Check the service state:

```powershell
docker compose ps
docker compose logs --tail=100 gateway auth ai-engine paddleocr invoice-service
```

Basic health checks:

```powershell
curl.exe http://localhost:8000/health
curl.exe http://localhost:8001/health
curl.exe http://localhost:8002/health
curl.exe http://localhost:8003/health
curl.exe http://localhost:8004/health
curl.exe http://localhost:8005/health
curl.exe http://localhost:8006/health
curl.exe http://localhost:8007/health
curl.exe http://localhost:8008/health
curl.exe http://localhost:8009/health
curl.exe http://localhost:8010/health
curl.exe http://localhost:8011/health
curl.exe http://localhost:8013/health
curl.exe http://localhost:8014/health
```

If an internal service is slow to become healthy, inspect its logs before changing application code. PaddleOCR model initialization is expected to be the slowest part.

To stop the stack without deleting persisted local volumes:

```powershell
cd backend/infra
docker compose down
```

Do not use `docker compose down -v` unless intentionally resetting all local databases, MinIO files, Redis data, and test state.

## Start the frontend

In a second terminal from the repository root:

```powershell
cd frontend
bun install
bun run dev
```

Open `http://localhost:8080`. The frontend proxy expects the Gateway on port 8000. Starting only the Invoice Service or only the AI Engine is not enough for the normal browser workflow.

Use the application signup page to create a local development company, then sign in. The access token is held in memory and the refresh token uses an httpOnly cookie.

## Supplied petty-cash fixture

The shared fixture should be supplied separately as a zip and extracted into this exact location:

```text
test-data/june-2026-petty-cash/
  README.md
  receipts/
    ...receipt and invoice images/PDFs...
```

If the folder already exists, compare the extracted files before replacing anything. Do not commit private receipt images or the zip unless the project owner explicitly approves that. This fixture is not automatically read by Docker or by the frontend.

The fixture README and the manually verified ledger are the ground truth for expected date, amount, particulars/vendor, and category. Do not assume a filename is the invoice number. Match each result to the correct ground-truth row.

## End-to-end Scanner validation

Use the normal product path:

1. Open `/app/scanner`.
2. Upload one receipt first and confirm the request reaches the Gateway and Invoice Service.
3. Confirm Invoice Service calls AI Engine over internal HTTP.
4. Confirm AI Engine runs the document preprocessing and LiteParse/PaddleOCR path. The configured fallback behavior is documented in `backend/services/ai-engine/.env.example` and `docs/invoice-ocr-plan.md`.
5. Check extracted date, total, vendor/particulars, document type, line items where available, and suggested category.
6. Use the Needs Review workflow to correct or validate uncertain results. Do not post an unverified result as correct just to move the test forward.
7. Validate the invoice and use `Send to Accounting` where appropriate. This hands the purchase invoice to Transactions Service as an Expense.
8. Open `/app/records` and confirm the item appears in Saved Records with its category, amount, date, preview, and accounting state.
9. Check category grouping, category totals, per-record preview, and category PDF export when testing the complete Saved Records flow.
10. Repeat for the supplied receipts, recording clean matches, missing fields, incorrect fields, false positives, and uncategorized results.

A successful health check is not a successful end-to-end test. The result must be visible in the correct product area and traceable to the expected ground truth.

## Golden evaluation harness

For repeatable field-level OCR/extraction/category measurements, use the versioned evaluation harness:

```powershell
cd backend/eval
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e . -e ..\libs\ocr -e ..\libs\invoice_extraction
python cli.py --detail
```

The evaluator requires the live AI Engine at `http://localhost:8007` and scans the fixture receipts through the real extraction endpoint. It compares vendor, invoice date, total, category, document type, and transactional classification. It does not create Saved Records; use the frontend workflow above for persistence and accounting validation.

For regression tracking:

```powershell
python cli.py --compare-baseline baselines/2026-09-04-post-p0-financial-safety-hardening.json
```

Save new baselines only when the owner requests it, and explain any accuracy change in the handoff report.

## Troubleshooting rules

- If the frontend cannot load data, check Gateway logs and ensure it is running on port 8000.
- If login fails or downstream requests return 401, confirm the same `JWT_SECRET_KEY` is used everywhere that verifies tokens.
- If OCR fails, inspect `ai-engine` and `paddleocr` logs first; do not bypass them with a fake extraction response.
- If the stack reports database connection errors, check Docker health and the service-specific database hostname/port values.
- If MinIO-backed previews fail, check `minio`, `minio-init`, and the relevant service bucket configuration.
- If a receipt lands in Needs Review, that is an expected safety state. Investigate the extracted fields and ground truth before changing classification or validation rules.
- Do not add RabbitMQ, an LLM, a shared database, or a second upload path unless the owner explicitly requests an architecture change.

## Final handoff report

After setup and testing, report:

- Git commit tested and whether the working tree is clean.
- Docker services that started successfully and any that required attention.
- Environment variables configured, naming only the variables; never print secret values.
- Number of fixture documents processed.
- Extraction/category results and any mismatches against ground truth.
- Whether corrected records reached Saved Records and Transactions Service.
- Any code or configuration changes made, with file paths.
- Commands that another developer can repeat.

Never include passwords, JWT values, Fernet keys, OAuth client secrets, access tokens, private mailbox data, or unredacted receipt content in the report.
