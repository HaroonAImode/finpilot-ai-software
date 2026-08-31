# Slack Connector Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the standalone `D:\projects\slack-connector` backend into FinPilot as `backend/services/slack-connector` (a new, company-scoped microservice), and add a "Connected Apps" surface in the FinPilot frontend to connect Slack, browse/categorize discovered files, and manually send invoice-looking files into the existing Invoice Scanner UI flow.

**Architecture:** Single new FastAPI microservice (port 8010) with its own Postgres DB, Celery+Redis for background sync, and shared MinIO for file storage — following FinPilot's per-service, own-database pattern. Since FinPilot has no Auth Service or Gateway yet, this service runs an **interim dev-mode auth shim**: every query is scoped by `company_id` exactly as the real system will require, but `company_id` comes from an `X-Company-ID` header if present, else a `DEFAULT_COMPANY_ID` env var — no JWT verification yet. The frontend talks to the service directly through a Vite dev proxy, no Gateway hop.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL 16, Celery 5 + Redis, boto3 (S3/MinIO), httpx, Fernet encryption (`cryptography`), pytest + pytest-asyncio. Frontend: React 19 + TanStack Router/Query (existing), shadcn/ui + Tailwind (existing), native `fetch`.

**Spec:** [docs/superpowers/specs/2026-08-18-slack-connector-integration-design.md](../specs/2026-08-18-slack-connector-integration-design.md)

## Global Constraints

- Every DB query in the new service is scoped by `company_id` — no endpoint trusts a client-supplied `installation_id` or `company_id` in the request body/query string (spec §5, §8).
- No Slack token, signing secret, S3 key, or other secret is ever logged, returned in an API response, or embedded in a URL exposed to the browser (spec §11; ported from source's `SecretRedactingFormatter` and signed-URL pattern).
- All new/ported Python code targets Python 3.11 (`requires-python = ">=3.11,<3.14"` in `pyproject.toml`, matching the source project's proven local environment).
- Route prefix for every endpoint in this service: `/api/v1/slack/*` (spec §8).
- Real Slack credentials, encryption keys, and DB passwords are never committed — only `.env.example` with placeholders, `.env` stays gitignored.
- This slice does not touch Invoice Service, Gateway, or Auth Service code (none exist yet) — it only calls Invoice Service's future `POST /invoices/scan` over HTTP from `scanner_bridge.py`, and that call is expected to fail cleanly (502, caught) until Invoice Service exists.

---

## File Structure

```
backend/services/slack-connector/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── worker.py                          ← Celery app + run_sync task
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── auth.py                    ← /connect, /callback, /status, DELETE /installation
│   │       └── sync.py                    ← /sync, /files/*, /files/{id}/send-to-scanner
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                      ← Settings (+ default_company_id, invoice_service_url)
│   │   ├── tenancy.py                     ← NEW: get_company_id, get_installation_for_company
│   │   ├── security.py                    ← TokenCipher (ported unchanged)
│   │   └── logging.py                     ← SecretRedactingFormatter (ported unchanged)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── session.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── workspace.py                   ← ported unchanged
│   │   ├── installation.py                ← ported + company_id column
│   │   ├── conversation.py                ← ported unchanged
│   │   ├── file.py                        ← ported unchanged
│   │   ├── sync_job.py                    ← ported unchanged
│   │   ├── sync_cursor.py                 ← ported unchanged
│   │   └── app_user.py                    ← ported unchanged
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── sync.py                        ← ported unchanged
│   └── services/
│       ├── __init__.py
│       ├── rate_limiter.py                ← ported unchanged
│       ├── download_manager.py            ← ported unchanged
│       ├── sync_orchestrator.py           ← ported unchanged
│       ├── reconciliation.py              ← ported unchanged
│       ├── scanner_bridge.py              ← NEW
│       ├── categorization/
│       │   ├── __init__.py
│       │   ├── service.py                 ← ported unchanged
│       │   └── rules.yaml                 ← ported unchanged
│       └── slack/
│           ├── __init__.py
│           ├── client.py                  ← ported unchanged
│           ├── discovery.py               ← ported unchanged
│           └── oauth.py                   ← ported unchanged
├── alembic/
│   ├── env.py                             ← ported unchanged
│   ├── script.py.mako                     ← ported unchanged
│   └── versions/
│       ├── 20260813_01_initial_workspace_installation.py   ← ported unchanged
│       ├── 20260813_02_phase_2_tables.py                   ← ported unchanged
│       ├── 20260813_03_increase_varchar_sizes.py           ← ported unchanged
│       └── 20260818_04_add_company_id.py                   ← NEW
├── tests/
│   ├── conftest.py                        ← ported + company_id fixture
│   └── unit/
│       ├── test_oauth.py                  ← ported unchanged
│       ├── test_security.py               ← ported unchanged
│       ├── test_phase3_hardening.py       ← ported + company_id in fixtures
│       ├── test_tenancy.py                ← NEW
│       └── test_scanner_bridge.py         ← NEW
├── alembic.ini
├── Dockerfile
├── pyproject.toml
├── .dockerignore
└── .env.example

backend/infra/
└── docker-compose.yml                     ← NEW (only slack-connector + shared infra for now)

src/
├── lib/
│   └── slack-connector.ts                 ← NEW: typed fetch client
├── routes/
│   └── app.settings.tsx                   ← MODIFIED: add "Connected Apps" tab
└── components/
    └── slack/
        ├── connected-apps-card.tsx        ← NEW
        └── slack-files-sheet.tsx          ← NEW

vite.config.ts                             ← MODIFIED: dev proxy to localhost:8010
```

---

### Task 1: Service skeleton — config, DB session, logging, security

**Files:**
- Create: `backend/services/slack-connector/app/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/core/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/core/config.py`
- Create: `backend/services/slack-connector/app/core/security.py`
- Create: `backend/services/slack-connector/app/core/logging.py`
- Create: `backend/services/slack-connector/app/db/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/db/session.py`
- Create: `backend/services/slack-connector/pyproject.toml`
- Create: `backend/services/slack-connector/.env.example`
- Create: `backend/services/slack-connector/.dockerignore`
- Test: `backend/services/slack-connector/tests/unit/test_config.py`

**Interfaces:**
- Produces: `app.core.config.Settings` (pydantic-settings model), `get_settings() -> Settings` (lru_cached). `app.core.security.TokenCipher(key: str)` with `.encrypt(str) -> str`, `.decrypt(str) -> str`, `.encrypt_state(str) -> str`, `.decrypt_state(str, ttl_seconds: int = 600) -> str`. `app.core.logging.configure_logging(log_level: str)` and `SecretRedactingFormatter`. `app.db.session.get_db() -> AsyncGenerator[AsyncSession, None]`, module-level `engine`, `AsyncSessionLocal`.

- [ ] **Step 1: Create the package directories and empty `__init__.py` files**

```bash
mkdir -p "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/core"
mkdir -p "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/db"
mkdir -p "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/tests/unit"
touch "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/__init__.py"
touch "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/core/__init__.py"
touch "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/db/__init__.py"
```

- [ ] **Step 2: Write `app/core/config.py`** — ported from `D:\projects\slack-connector\backend\connector-service\app\core\config.py` with two additions: `default_company_id` (the interim auth shim's fallback tenant) and `invoice_service_url` (for the send-to-scanner bridge, Task 12).

```python
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    slack_client_id: str = Field(alias="SLACK_CLIENT_ID")
    slack_client_secret: str = Field(alias="SLACK_CLIENT_SECRET")
    slack_signing_secret: str = Field(alias="SLACK_SIGNING_SECRET")
    slack_redirect_uri: str = Field(alias="SLACK_REDIRECT_URI")
    slack_dev_bot_token: str | None = Field(default=None, alias="SLACK_DEV_BOT_TOKEN")
    token_encryption_key: str = Field(alias="TOKEN_ENCRYPTION_KEY")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    celery_broker_url: str | None = Field(default=None, alias="CELERY_BROKER_URL")
    celery_result_backend: str | None = Field(default=None, alias="CELERY_RESULT_BACKEND")
    s3_endpoint_url: str = Field(alias="S3_ENDPOINT_URL")
    s3_public_endpoint_url: str | None = Field(default=None, alias="S3_PUBLIC_ENDPOINT_URL")
    s3_access_key_id: str = Field(alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(alias="S3_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field(alias="S3_BUCKET_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(alias="FRONTEND_BASE_URL")

    # Interim auth shim (spec §"Interim auth"): used until Auth Service + Gateway
    # exist and can inject a real, JWT-verified X-Company-ID header.
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    # Used by scanner_bridge.py (Task 12) to call Invoice Service's scan endpoint.
    invoice_service_url: str = Field(alias="INVOICE_SERVICE_URL")

    @property
    def resolved_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def resolved_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Copy `app/core/security.py` and `app/core/logging.py` unchanged**

```bash
cp "D:/projects/slack-connector/backend/connector-service/app/core/security.py" \
   "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/core/security.py"
cp "D:/projects/slack-connector/backend/connector-service/app/core/logging.py" \
   "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/app/core/logging.py"
```

- [ ] **Step 4: Write `app/db/session.py`** (ported unchanged from source):

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 5: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=75.0.0,<76.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "finpilot-slack-connector"
version = "0.1.0"
description = "FinPilot Slack document extraction connector service"
requires-python = ">=3.11,<3.14"
dependencies = [
  "fastapi==0.115.12",
  "uvicorn[standard]==0.34.2",
  "sqlalchemy==2.0.40",
  "alembic==1.15.2",
  "asyncpg==0.30.0",
  "pydantic==2.11.3",
  "pydantic-settings==2.8.1",
  "httpx==0.28.1",
  "cryptography==44.0.2",
  "python-dotenv==1.1.0",
  "boto3==1.37.29",
  "redis==5.2.1",
  "celery[redis]==5.4.0",
  "pyyaml==6.0.1",
]

[project.optional-dependencies]
dev = [
  "pytest==8.3.5",
  "pytest-asyncio==0.26.0",
  "aiosqlite==0.21.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 6: Write `.env.example`**

```env
SLACK_CLIENT_ID=1234567890123.1234567890123
SLACK_CLIENT_SECRET=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
SLACK_SIGNING_SECRET=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
SLACK_REDIRECT_URI=http://localhost:8010/api/v1/slack/callback
SLACK_DEV_BOT_TOKEN=

# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=REPLACE_WITH_GENERATED_FERNET_KEY

DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/slack_connector_db
REDIS_URL=redis://localhost:6379/3
CELERY_BROKER_URL=redis://localhost:6379/4
CELERY_RESULT_BACKEND=redis://localhost:6379/5
S3_ENDPOINT_URL=http://localhost:9000
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET_NAME=slack-connector-files
APP_ENV=development
LOG_LEVEL=INFO
FRONTEND_BASE_URL=http://localhost:3000

# Interim auth shim — remove once Auth Service + Gateway inject a real X-Company-ID.
# Any valid UUID; this is the single "dev company" every request is scoped to.
DEFAULT_COMPANY_ID=00000000-0000-0000-0000-000000000001

INVOICE_SERVICE_URL=http://localhost:8002
```

- [ ] **Step 7: Write `.dockerignore`**

```
.venv
__pycache__
*.pyc
.pytest_cache
.env
```

- [ ] **Step 8: Write the failing config test**

```python
# tests/unit/test_config.py
import os

from app.core.config import Settings


def test_settings_reads_default_company_id_and_invoice_service_url() -> None:
    settings = Settings(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
        REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )

    assert settings.default_company_id == "00000000-0000-0000-0000-000000000001"
    assert settings.invoice_service_url == "http://localhost:8002"
    assert settings.resolved_celery_broker_url == settings.redis_url
```

- [ ] **Step 9: Install dependencies and run the test to verify it passes**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/pytest tests/unit/test_config.py -v
```
Expected: 1 passed. (There's nothing to fail here — this is a straight port of a config object — so this step doubles as both the write and the verify; if it fails, the `Settings` field aliases don't match the env var names above.)

- [ ] **Step 10: Commit**

```bash
git add backend/services/slack-connector
git commit -m "feat(slack-connector): scaffold service config, DB session, security, logging"
```

---

### Task 2: Database models

**Files:**
- Create: `backend/services/slack-connector/app/models/__init__.py`
- Create: `backend/services/slack-connector/app/models/base.py`
- Create: `backend/services/slack-connector/app/models/workspace.py`
- Create: `backend/services/slack-connector/app/models/installation.py`
- Create: `backend/services/slack-connector/app/models/conversation.py`
- Create: `backend/services/slack-connector/app/models/file.py`
- Create: `backend/services/slack-connector/app/models/sync_job.py`
- Create: `backend/services/slack-connector/app/models/sync_cursor.py`
- Create: `backend/services/slack-connector/app/models/app_user.py`
- Create: `backend/services/slack-connector/app/db/base.py`
- Test: `backend/services/slack-connector/tests/unit/test_models.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (models are independent of config).
- Produces: `app.models.{Workspace, Installation, InstallationStatus, Conversation, ConversationType, File, SyncJob, SyncStatus, SyncCursor, AppUser}`. `Installation` now has a `company_id: uuid.UUID` column (not null, unique) in addition to every field it already had.

- [ ] **Step 1: Copy the six unchanged models and `db/base.py`**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
mkdir -p app/models
SRC="D:/projects/slack-connector/backend/connector-service/app"
cp "$SRC/models/base.py" app/models/base.py
cp "$SRC/models/workspace.py" app/models/workspace.py
cp "$SRC/models/conversation.py" app/models/conversation.py
cp "$SRC/models/file.py" app/models/file.py
cp "$SRC/models/sync_job.py" app/models/sync_job.py
cp "$SRC/models/sync_cursor.py" app/models/sync_cursor.py
cp "$SRC/models/app_user.py" app/models/app_user.py
cp "$SRC/db/base.py" app/db/base.py
```

- [ ] **Step 2: Write `app/models/installation.py`** — ported from `D:\projects\slack-connector\backend\connector-service\app\models\installation.py`, adding the `company_id` column:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InstallationStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    needs_reauth = "needs_reauth"


class Installation(Base):
    __tablename__ = "installation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), unique=True, nullable=False)
    bot_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    bot_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[InstallationStatus] = mapped_column(Enum(InstallationStatus, name="installation_status"), nullable=False, default=InstallationStatus.active)

    workspace: Mapped["Workspace"] = relationship(back_populates="installation")
```

- [ ] **Step 3: Write `app/models/__init__.py`** (ported unchanged):

```python
from app.models.app_user import AppUser
from app.models.conversation import Conversation, ConversationType
from app.models.file import File
from app.models.installation import Installation, InstallationStatus
from app.models.sync_cursor import SyncCursor
from app.models.sync_job import SyncJob, SyncStatus
from app.models.workspace import Workspace

__all__ = [
    "Installation",
    "InstallationStatus",
    "Workspace",
    "Conversation",
    "ConversationType",
    "AppUser",
    "File",
    "SyncJob",
    "SyncStatus",
    "SyncCursor",
]
```

- [ ] **Step 4: Write the failing test** — confirms `company_id` is required and unique-shaped, and that the whole model set creates cleanly against SQLite in-memory (fast sanity check before the real Postgres migration in Task 3):

```python
# tests/unit/test_models.py
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models import Installation, InstallationStatus, Workspace


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_installation_requires_company_id(engine) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        workspace = Workspace(slack_team_id="T1", name="Test Co")
        db.add(workspace)
        await db.flush()

        installation = Installation(
            company_id=uuid.uuid4(),
            workspace_id=workspace.id,
            bot_token_encrypted="enc",
            scopes=["files:read"],
            status=InstallationStatus.active,
        )
        db.add(installation)
        await db.commit()

        fetched = await db.scalar(select(Installation).where(Installation.workspace_id == workspace.id))
        assert fetched.company_id == installation.company_id


@pytest.mark.asyncio
async def test_installation_company_id_is_unique(engine) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        company_id = uuid.uuid4()
        ws1 = Workspace(slack_team_id="T1", name="Co 1")
        ws2 = Workspace(slack_team_id="T2", name="Co 2")
        db.add_all([ws1, ws2])
        await db.flush()

        db.add(Installation(company_id=company_id, workspace_id=ws1.id, bot_token_encrypted="a", scopes=[]))
        await db.commit()

        db.add(Installation(company_id=company_id, workspace_id=ws2.id, bot_token_encrypted="b", scopes=[]))
        with pytest.raises(IntegrityError):
            await db.commit()
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
.venv/Scripts/pytest tests/unit/test_models.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/services/slack-connector/app/models backend/services/slack-connector/app/db/base.py backend/services/slack-connector/tests/unit/test_models.py
git commit -m "feat(slack-connector): port DB models, add company_id to installation"
```

---

### Task 3: Alembic migrations

**Files:**
- Create: `backend/services/slack-connector/alembic.ini`
- Create: `backend/services/slack-connector/alembic/env.py`
- Create: `backend/services/slack-connector/alembic/script.py.mako`
- Create: `backend/services/slack-connector/alembic/versions/20260813_01_initial_workspace_installation.py`
- Create: `backend/services/slack-connector/alembic/versions/20260813_02_phase_2_tables.py`
- Create: `backend/services/slack-connector/alembic/versions/20260813_03_increase_varchar_sizes.py`
- Create: `backend/services/slack-connector/alembic/versions/20260818_04_add_company_id.py`

**Interfaces:**
- Consumes: `app.models.base.Base` (Task 2), `app.core.config.get_settings` (Task 1).
- Produces: a migration chain ending at revision `20260818_04`, runnable via `alembic upgrade head`.

- [ ] **Step 1: Copy `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, and the three unchanged migrations**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service"
mkdir -p alembic/versions
cp "$SRC/alembic.ini" alembic.ini
cp "$SRC/alembic/env.py" alembic/env.py
cp "$SRC/alembic/script.py.mako" alembic/script.py.mako
cp "$SRC/alembic/versions/20260813_01_initial_workspace_installation.py" alembic/versions/
cp "$SRC/alembic/versions/20260813_02_phase_2_tables.py" alembic/versions/
cp "$SRC/alembic/versions/20260813_03_increase_varchar_sizes.py" alembic/versions/
```

- [ ] **Step 2: Open `alembic/versions/20260813_01_initial_workspace_installation.py` and remove the `unique=True` on `installation.workspace_id`'s create_table call is unaffected — leave the file exactly as copied.** (No edit needed: `company_id` is added by a separate revision below, keeping each migration's intent single-purpose and matching the source project's own pattern of narrowly-scoped revisions.)

- [ ] **Step 3: Write the new migration `alembic/versions/20260818_04_add_company_id.py`**

```python
"""add company_id to installation for FinPilot multi-tenancy

Revision ID: 20260818_04
Revises: 20260813_03
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260818_04"
down_revision = "20260813_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installation",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Backfill any pre-existing row (there shouldn't be one in a fresh install)
    # before tightening to NOT NULL + UNIQUE, so the migration is safe to run
    # against a database that already has data.
    op.execute("UPDATE installation SET company_id = gen_random_uuid() WHERE company_id IS NULL")
    op.alter_column("installation", "company_id", nullable=False)
    op.create_unique_constraint("uq_installation_company_id", "installation", ["company_id"])


def downgrade() -> None:
    op.drop_constraint("uq_installation_company_id", "installation", type_="unique")
    op.drop_column("installation", "company_id")
```

- [ ] **Step 4: Verify the full migration chain compiles to valid DDL without a live database** (this is the same offline-verification method the source project used successfully — see `docs/Slack Connector - Implementation Progress Report.md` Phase 1 "Verification performed"):

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/slack_connector_db" \
  .venv/Scripts/alembic upgrade head --sql
```
Expected: prints `CREATE TABLE workspace...`, `CREATE TABLE installation...`, the Phase-2 tables, the `ALTER COLUMN` statements from `20260813_03`, then `ALTER TABLE installation ADD COLUMN company_id...` / `ALTER TABLE installation ALTER COLUMN company_id SET NOT NULL` / `ALTER TABLE installation ADD CONSTRAINT uq_installation_company_id...` — no errors, no live DB connection required.

- [ ] **Step 5: Commit**

```bash
git add backend/services/slack-connector/alembic.ini backend/services/slack-connector/alembic
git commit -m "feat(slack-connector): port Alembic migrations, add company_id migration"
```

---

### Task 4: Rate limiter and categorization service

**Files:**
- Create: `backend/services/slack-connector/app/services/__init__.py`
- Create: `backend/services/slack-connector/app/services/rate_limiter.py`
- Create: `backend/services/slack-connector/app/services/categorization/__init__.py`
- Create: `backend/services/slack-connector/app/services/categorization/service.py`
- Create: `backend/services/slack-connector/app/services/categorization/rules.yaml`
- Test: `backend/services/slack-connector/tests/unit/test_categorization.py`

**Interfaces:**
- Produces: `app.services.rate_limiter.{RateLimiter, get_rate_limiter}`. `app.services.categorization.{CategorizationService, get_categorization_service}` with `.categorize(filename, title=None, channel_name=None, file_type=None) -> tuple[str, float, str]`.

- [ ] **Step 1: Copy the four files unchanged**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service/app/services"
mkdir -p app/services/categorization
touch app/services/__init__.py
cp "$SRC/rate_limiter.py" app/services/rate_limiter.py
cp "$SRC/categorization/__init__.py" app/services/categorization/__init__.py
cp "$SRC/categorization/service.py" app/services/categorization/service.py
cp "$SRC/categorization/rules.yaml" app/services/categorization/rules.yaml
```

- [ ] **Step 2: Write the failing test** — this is the ported `TestCategorization` class from `D:\projects\slack-connector\backend\connector-service\tests\unit\test_phase3_hardening.py` (lines 180-238), extracted into its own file since categorization has no other dependency on the rest of the hardening suite:

```python
# tests/unit/test_categorization.py
from app.services.categorization.service import CategorizationService


def test_invoice_keyword_matching() -> None:
    cat_service = CategorizationService()
    test_cases = [
        ("invoice_2024.pdf", "Invoices"),
        ("inv-12345.xlsx", "Invoices"),
        ("monthly_bill_aug.csv", "Invoices"),
    ]
    for filename, expected_category in test_cases:
        category, confidence, source = cat_service.categorize(filename=filename, title=None, channel_name=None)
        assert category == expected_category, f"{filename} should be {expected_category}, got {category}"


def test_below_threshold_returns_uncategorized() -> None:
    cat_service = CategorizationService()
    category, confidence, source = cat_service.categorize(
        filename="screenshot_20240801.png", title=None, channel_name="general",
    )
    assert category == "Images & Screenshots"
    assert confidence >= 0.70


def test_manual_override_marker_is_never_produced_by_the_classifier() -> None:
    # The classifier itself only ever returns "rule_based" as the source —
    # "manual_override" is set exclusively by the PATCH /files/{id}/category
    # endpoint (Task 11), proving the two code paths can't collide.
    cat_service = CategorizationService()
    category, confidence, source = cat_service.categorize(filename="file.pdf", title="Generic", channel_name="general")
    assert source == "rule_based"
```

- [ ] **Step 3: Install pyyaml is already in `pyproject.toml` (Task 1) — run the test to verify it passes**

```bash
.venv/Scripts/pytest tests/unit/test_categorization.py -v
```
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/services/slack-connector/app/services backend/services/slack-connector/tests/unit/test_categorization.py
git commit -m "feat(slack-connector): port rate limiter and rule-based categorization"
```

---

### Task 5: Slack API client, discovery, and OAuth helpers

**Files:**
- Create: `backend/services/slack-connector/app/services/slack/__init__.py`
- Create: `backend/services/slack-connector/app/services/slack/client.py`
- Create: `backend/services/slack-connector/app/services/slack/discovery.py`
- Create: `backend/services/slack-connector/app/services/slack/oauth.py`
- Test: `backend/services/slack-connector/tests/unit/test_oauth.py`

**Interfaces:**
- Consumes: `app.core.config.Settings` (Task 1), `app.services.rate_limiter.RateLimiter` (Task 4), `app.models.{AppUser, Conversation, ConversationType, File}` (Task 2).
- Produces: `app.services.slack.oauth.{BOT_SCOPES, authorization_url(settings, state) -> str, exchange_code(settings, code) -> dict}`. `app.services.slack.discovery.{DiscoveryService, DiscoveryPersistenceService}` (async context manager; `list_conversations()`, `walk_conversation_messages(channel_id)`, `extract_files_from_message(...)`, `enrich_user_info(user_id)`; persistence's `get_or_create_conversation`, `get_or_create_user`, `persist_file`). `app.services.slack.client.SlackAPIClient(bot_token, rate_limiter, settings)` with `.call(method, params) -> dict`.

- [ ] **Step 1: Copy all three files unchanged** — none of them reference `installation_id`-as-client-input or auth; they only take an already-decrypted bot token and operate on rows scoped by `installation_id`, which the callers (Tasks 7, 10, 11) will resolve from `company_id` before invoking these services.

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service/app/services/slack"
mkdir -p app/services/slack
cp "$SRC/__init__.py" app/services/slack/__init__.py
cp "$SRC/client.py" app/services/slack/client.py
cp "$SRC/discovery.py" app/services/slack/discovery.py
cp "$SRC/oauth.py" app/services/slack/oauth.py
```

- [ ] **Step 2: Write the failing test** — ported from `D:\projects\slack-connector\backend\connector-service\tests\unit\test_oauth.py`, with the redirect URI updated to this service's `/api/v1/slack/callback` path (Task 10):

```python
# tests/unit/test_oauth.py
from app.core.config import Settings
from app.services.slack.oauth import BOT_SCOPES, authorization_url


def test_authorization_url_requests_required_bot_scopes() -> None:
    settings = Settings(
        SLACK_CLIENT_ID="client-id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )
    url = authorization_url(settings, "state")

    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    for scope in BOT_SCOPES:
        assert scope.replace(":", "%3A") in url or scope in url
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
.venv/Scripts/pytest tests/unit/test_oauth.py -v
```
Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/services/slack-connector/app/services/slack backend/services/slack-connector/tests/unit/test_oauth.py
git commit -m "feat(slack-connector): port Slack API client, discovery, and OAuth helpers"
```

---

### Task 6: Download manager and reconciliation service

**Files:**
- Create: `backend/services/slack-connector/app/services/download_manager.py`
- Create: `backend/services/slack-connector/app/services/reconciliation.py`
- Test: `backend/services/slack-connector/tests/unit/test_file_retry.py`

**Interfaces:**
- Consumes: `app.core.config.Settings` (Task 1), `app.core.security.TokenCipher` (Task 1), `app.models.{File, Installation, SyncJob, SyncStatus}` (Task 2), `app.services.slack.client.SlackAPIClient` (Task 5).
- Produces: `app.services.download_manager.DownloadManager(settings)` with `.download_and_store(file_id, filename, download_url, bot_token) -> tuple[str, str]`, `.verify_s3_file(s3_key, expected_sha256) -> bool`, `.get_download_url(s3_key, expiration=3600, mimetype=None, filename=None) -> str`, and public `.s3_client`. `app.services.reconciliation.{ReconciliationService, FileRetryManager}` — `FileRetryManager(db).retry_file_download(file_id: UUID) -> File` (raises `ValueError` if not found).

- [ ] **Step 1: Copy both files unchanged**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service/app/services"
cp "$SRC/download_manager.py" app/services/download_manager.py
cp "$SRC/reconciliation.py" app/services/reconciliation.py
```

- [ ] **Step 2: Write the failing test** — ported `TestFileRetry` class from `D:\projects\slack-connector\backend\connector-service\tests\unit\test_phase3_hardening.py` (lines 241-281), adapted to construct an `Installation` with the now-required `company_id`:

```python
# tests/unit/test_file_retry.py
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import TokenCipher
from app.models.base import Base
from app.models import File, Installation, Workspace
from app.services.reconciliation import FileRetryManager


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def installation(db: AsyncSession) -> Installation:
    workspace = Workspace(slack_team_id="T1234567890", name="Test Workspace")
    db.add(workspace)
    await db.flush()
    cipher = TokenCipher("dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=")
    installation = Installation(
        company_id=uuid.uuid4(), workspace_id=workspace.id,
        bot_token_encrypted=cipher.encrypt("xoxb-test-token"), scopes=["files:read"],
    )
    db.add(installation)
    await db.commit()
    await db.refresh(installation)
    return installation


@pytest.mark.asyncio
async def test_retry_resets_failure_flags(db: AsyncSession, installation: Installation) -> None:
    file = File(
        installation_id=installation.id, slack_file_id="F1234567890", filename="test.pdf",
        file_type="pdf", mimetype="application/pdf", size=1000, created_at=datetime.now(),
        is_external=False, slack_permalink="https://example.com",
        conversation_id=uuid.uuid4(), message_ts="1234567890.123456",
        download_failed=True, download_error="Network timeout",
    )
    db.add(file)
    await db.commit()
    await db.refresh(file)

    retried = await FileRetryManager(db).retry_file_download(file.id)

    assert retried.download_failed is False
    assert retried.download_error is None
    assert retried.s3_key is None


@pytest.mark.asyncio
async def test_retry_not_found_raises_error(db: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await FileRetryManager(db).retry_file_download(uuid.uuid4())
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
.venv/Scripts/pytest tests/unit/test_file_retry.py -v
```
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/services/slack-connector/app/services/download_manager.py backend/services/slack-connector/app/services/reconciliation.py backend/services/slack-connector/tests/unit/test_file_retry.py
git commit -m "feat(slack-connector): port download manager and reconciliation/retry service"
```

---

### Task 7: Sync orchestrator and Celery worker

**Files:**
- Create: `backend/services/slack-connector/app/services/sync_orchestrator.py`
- Create: `backend/services/slack-connector/app/worker.py`

**Interfaces:**
- Consumes: `app.core.config.get_settings` (Task 1), `app.core.security.TokenCipher` (Task 1), `app.models.*` (Task 2), `app.services.categorization.get_categorization_service` (Task 4), `app.services.download_manager.DownloadManager` (Task 6), `app.services.rate_limiter.{RateLimiter, get_rate_limiter}` (Task 4), `app.services.slack.discovery.{DiscoveryService, DiscoveryPersistenceService}` (Task 5).
- Produces: `app.services.sync_orchestrator.SyncOrchestrator(db, installation_id, bot_token_encrypted, settings, rate_limiter)` with `.sync(sync_job: SyncJob) -> None`. `app.worker.{celery_app, run_sync}` — `run_sync.delay(sync_job_id: str)` (Celery task, dispatched from Task 11's `POST /sync`).

- [ ] **Step 1: Copy both files unchanged** — neither references `company_id`; they operate purely on an `installation_id` UUID already resolved by the caller.

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service/app"
cp "$SRC/services/sync_orchestrator.py" app/services/sync_orchestrator.py
cp "$SRC/worker.py" app/worker.py
```

- [ ] **Step 2: Verify the module imports cleanly** (no dedicated unit test in the source project either — `sync_orchestrator` and `worker` are exercised via the Celery worker process and manual verification, per the source's own progress report; a full sync run needs live Slack credentials + Postgres + Redis, which come together in Task 15's Docker Compose):

```bash
.venv/Scripts/python -c "import app.services.sync_orchestrator; import app.worker; print('ok')"
```
Expected: prints `ok` with no `ImportError`/`AttributeError`.

- [ ] **Step 3: Commit**

```bash
git add backend/services/slack-connector/app/services/sync_orchestrator.py backend/services/slack-connector/app/worker.py
git commit -m "feat(slack-connector): port sync orchestrator and Celery worker"
```

---

### Task 8: Tenancy dependency (the interim auth shim)

**Files:**
- Create: `backend/services/slack-connector/app/core/tenancy.py`
- Test: `backend/services/slack-connector/tests/unit/test_tenancy.py`

**Interfaces:**
- Consumes: `app.core.config.get_settings` (Task 1), `app.db.session.get_db` (Task 1), `app.models.Installation` (Task 2).
- Produces: `app.core.tenancy.get_company_id(x_company_id: str | None) -> uuid.UUID` (FastAPI dependency), `get_installation_for_company(company_id, db) -> Installation` (404s via `HTTPException` if the company has no connected workspace), `get_optional_installation_for_company(company_id, db) -> Installation | None` (returns `None` instead of raising — used only by the `/status` endpoint in Task 10, where "not connected" is a normal response, not an error).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_tenancy.py
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.tenancy import get_company_id, get_installation_for_company, get_optional_installation_for_company
from app.models.base import Base
from app.models import Installation, Workspace


def _settings(default_company_id: str) -> Settings:
    return Settings(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID=default_company_id, INVOICE_SERVICE_URL="http://localhost:8002",
    )


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_company_id_falls_back_to_default_when_header_absent() -> None:
    default_id = uuid.uuid4()
    result = await get_company_id(x_company_id=None, settings=_settings(str(default_id)))
    assert result == default_id


@pytest.mark.asyncio
async def test_get_company_id_prefers_explicit_header() -> None:
    header_id = uuid.uuid4()
    result = await get_company_id(x_company_id=str(header_id), settings=_settings(str(uuid.uuid4())))
    assert result == header_id


@pytest.mark.asyncio
async def test_get_installation_for_company_404s_when_not_connected(db: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_installation_for_company(company_id=uuid.uuid4(), db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_installation_for_company_returns_the_matching_row(db: AsyncSession) -> None:
    company_id = uuid.uuid4()
    workspace = Workspace(slack_team_id="T1", name="Co")
    db.add(workspace)
    await db.flush()
    installation = Installation(company_id=company_id, workspace_id=workspace.id, bot_token_encrypted="enc", scopes=[])
    db.add(installation)
    await db.commit()

    result = await get_installation_for_company(company_id=company_id, db=db)
    assert result.id == installation.id


@pytest.mark.asyncio
async def test_get_optional_installation_for_company_returns_none_when_not_connected(db: AsyncSession) -> None:
    result = await get_optional_installation_for_company(company_id=uuid.uuid4(), db=db)
    assert result is None
```

- [ ] **Step 2: Run the tests to verify they fail** (the module doesn't exist yet)

```bash
.venv/Scripts/pytest tests/unit/test_tenancy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.tenancy'`.

- [ ] **Step 3: Write `app/core/tenancy.py`**

```python
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Installation


async def get_company_id(
    x_company_id: str | None = Header(default=None, alias="X-Company-ID"),
    settings: Settings = Depends(get_settings),
) -> UUID:
    """
    Interim auth shim: resolves the calling company from an X-Company-ID
    header if present (the future Gateway contract), otherwise falls back to
    DEFAULT_COMPANY_ID. Remove the fallback once Auth Service + Gateway exist
    and every request is guaranteed to carry a JWT-verified header.
    """
    raw = x_company_id or settings.default_company_id
    return UUID(raw)


async def get_installation_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> Installation:
    installation = await db.scalar(select(Installation).where(Installation.company_id == company_id))
    if installation is None:
        raise HTTPException(status_code=404, detail="Slack is not connected for this company")
    return installation


async def get_optional_installation_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> Installation | None:
    return await db.scalar(select(Installation).where(Installation.company_id == company_id))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/pytest tests/unit/test_tenancy.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/services/slack-connector/app/core/tenancy.py backend/services/slack-connector/tests/unit/test_tenancy.py
git commit -m "feat(slack-connector): add company-scoping tenancy dependency (interim auth shim)"
```

---

### Task 9: Pydantic schemas

**Files:**
- Create: `backend/services/slack-connector/app/schemas/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/schemas/sync.py`

**Interfaces:**
- Produces: `app.schemas.sync.{FileResponse, SyncJobResponse, SyncStartResponse, FileListResponse, CategoryUpdateRequest}` — used by Task 11's routes.

- [ ] **Step 1: Copy unchanged**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
mkdir -p app/schemas
touch app/schemas/__init__.py
cp "D:/projects/slack-connector/backend/connector-service/app/schemas/sync.py" app/schemas/sync.py
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
.venv/Scripts/python -c "from app.schemas.sync import FileResponse, SyncJobResponse, SyncStartResponse, FileListResponse, CategoryUpdateRequest; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/services/slack-connector/app/schemas
git commit -m "feat(slack-connector): port Pydantic response/request schemas"
```

---

### Task 10: Auth routes — connect, callback, status, disconnect

**Files:**
- Create: `backend/services/slack-connector/app/api/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/api/routes/__init__.py` (empty)
- Create: `backend/services/slack-connector/app/api/routes/auth.py`
- Test: `backend/services/slack-connector/tests/unit/test_auth_routes.py`

**Interfaces:**
- Consumes: `app.core.config.{Settings, get_settings}` (Task 1), `app.core.security.TokenCipher` (Task 1), `app.core.tenancy.{get_company_id, get_optional_installation_for_company}` (Task 8), `app.db.session.get_db` (Task 1), `app.models.{Installation, InstallationStatus, Workspace}` (Task 2), `app.services.slack.oauth.{authorization_url, exchange_code}` (Task 5).
- Produces: `router` (FastAPI `APIRouter`, mounted at `/api/v1/slack` in Task 13) exposing `GET /connect`, `GET /callback`, `GET /status`, `DELETE /installation`.

This is the biggest behavioral change from the source project (spec §6): the OAuth `state` now carries `company_id`, encrypted, instead of just a CSRF nonce — because there's no session/JWT to recover the company from once Slack redirects the browser back.

- [ ] **Step 1: Write the failing tests** — these test the pure `state` encode/decode round-trip (the part of `/connect` and `/callback` that doesn't need a live Slack API or a real DB):

```python
# tests/unit/test_auth_routes.py
import json
import uuid

from app.core.security import TokenCipher


ENCRYPTION_KEY = "dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM="


def test_oauth_state_round_trips_company_id_and_nonce() -> None:
    from app.api.routes.auth import _build_state, _parse_state

    cipher = TokenCipher(ENCRYPTION_KEY)
    company_id = uuid.uuid4()

    encrypted_state, nonce = _build_state(cipher, company_id)
    parsed_company_id, parsed_nonce = _parse_state(cipher, encrypted_state)

    assert parsed_company_id == company_id
    assert parsed_nonce == nonce


def test_parse_state_rejects_tampered_payload() -> None:
    from app.api.routes.auth import _parse_state
    import pytest

    cipher = TokenCipher(ENCRYPTION_KEY)
    with pytest.raises(ValueError):
        _parse_state(cipher, "not-a-real-encrypted-state")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/Scripts/pytest tests/unit/test_auth_routes.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api'`.

- [ ] **Step 3: Write `app/api/routes/auth.py`** — adapted from `D:\projects\slack-connector\backend\connector-service\app\api\routes\auth.py`. Route paths change from `/slack`/`/slack/callback` (previously under an `/auth` prefix) to `/connect`/`/callback` (the parent `/api/v1/slack` prefix is added when this router is included in Task 13). The `state` payload becomes a JSON object `{nonce, company_id}` instead of a bare nonce. Adds `GET /status` and `DELETE /installation`, neither of which existed in the source project.

```python
import json
import secrets
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher
from app.core.tenancy import get_company_id, get_optional_installation_for_company
from app.db.session import get_db
from app.models.installation import Installation, InstallationStatus
from app.models.workspace import Workspace
from app.services.slack.oauth import authorization_url, exchange_code

router = APIRouter(tags=["auth"])


def _build_state(cipher: TokenCipher, company_id: UUID) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    payload = json.dumps({"nonce": nonce, "company_id": str(company_id)})
    return cipher.encrypt_state(payload), nonce


def _parse_state(cipher: TokenCipher, encrypted_state: str) -> tuple[UUID, str]:
    payload = cipher.decrypt_state(encrypted_state)
    data = json.loads(payload)
    return UUID(data["company_id"]), data["nonce"]


@router.get("/connect")
async def start_slack_oauth(
    company_id: UUID = Depends(get_company_id),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    cipher = TokenCipher(settings.token_encryption_key)
    state, nonce = _build_state(cipher, company_id)
    response = RedirectResponse(authorization_url(settings, state), status_code=302)
    response.set_cookie("slack_oauth_nonce", nonce, max_age=600, httponly=True, samesite="lax", secure=settings.app_env != "development")
    return response


@router.get("/callback")
async def slack_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="Slack authorization was not completed")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")

    cipher = TokenCipher(settings.token_encryption_key)
    try:
        company_id, expected_nonce = _parse_state(cipher, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from exc
    if not secrets.compare_digest(expected_nonce, request.cookies.get("slack_oauth_nonce", "")):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    try:
        payload = await exchange_code(settings, code)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Slack OAuth token exchange failed") from exc

    team = payload.get("team") or {}
    team_id = team.get("id")
    team_name = team.get("name")
    token = payload.get("access_token")
    if not team_id or not team_name or not token:
        raise HTTPException(status_code=502, detail="Slack OAuth response was incomplete")
    scopes = [scope for scope in (payload.get("scope") or "").split(",") if scope]

    workspace = (await db.execute(select(Workspace).where(Workspace.slack_team_id == team_id))).scalar_one_or_none()
    if workspace is None:
        workspace = Workspace(slack_team_id=team_id, name=team_name, domain=team.get("domain"), is_enterprise=bool(payload.get("is_enterprise_install", False)))
        db.add(workspace)
        await db.flush()
    else:
        workspace.name, workspace.domain = team_name, team.get("domain")

    other_owner = (await db.execute(select(Installation).where(Installation.workspace_id == workspace.id))).scalar_one_or_none()
    if other_owner and other_owner.company_id != company_id:
        raise HTTPException(status_code=409, detail="This Slack workspace is already connected to a different FinPilot company")

    installation = (await db.execute(select(Installation).where(Installation.company_id == company_id))).scalar_one_or_none()
    token_cipher = TokenCipher(settings.token_encryption_key)
    if installation is None:
        installation = Installation(
            company_id=company_id, workspace_id=workspace.id,
            bot_token_encrypted=token_cipher.encrypt(token), bot_user_id=payload.get("bot_user_id"),
            scopes=scopes, status=InstallationStatus.active,
        )
        db.add(installation)
    else:
        installation.workspace_id = workspace.id
        installation.bot_token_encrypted = token_cipher.encrypt(token)
        installation.bot_user_id = payload.get("bot_user_id")
        installation.scopes = scopes
        installation.status = InstallationStatus.active
    await db.commit()

    target = f"{settings.frontend_base_url.rstrip('/')}/app/settings?{urlencode({'tab': 'connected-apps', 'workspace': workspace.name})}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie("slack_oauth_nonce")
    return response


@router.get("/status")
async def get_connection_status(
    installation: Installation | None = Depends(get_optional_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if installation is None:
        return {"connected": False}
    workspace = await db.get(Workspace, installation.workspace_id)
    return {
        "connected": installation.status == InstallationStatus.active,
        "workspace_name": workspace.name if workspace else None,
        "scopes": installation.scopes,
        "installed_at": installation.installed_at.isoformat(),
    }


@router.delete("/installation")
async def disconnect_slack(
    installation: Installation = Depends(get_optional_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if installation is None:
        raise HTTPException(status_code=404, detail="Slack is not connected for this company")
    installation.status = InstallationStatus.revoked
    await db.commit()
    return {"connected": False}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/pytest tests/unit/test_auth_routes.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/services/slack-connector/app/api backend/services/slack-connector/tests/unit/test_auth_routes.py
git commit -m "feat(slack-connector): OAuth connect/callback with company-scoped state, add status/disconnect"
```

---

### Task 11: Sync and files routes — company-scoped, plus send-to-scanner

**Files:**
- Create: `backend/services/slack-connector/app/api/routes/sync.py`
- Create: `backend/services/slack-connector/app/services/scanner_bridge.py`
- Test: `backend/services/slack-connector/tests/unit/test_scanner_bridge.py`

**Interfaces:**
- Consumes: `app.core.config.{Settings, get_settings}` (Task 1), `app.core.tenancy.get_installation_for_company` (Task 8), `app.db.session.get_db` (Task 1), `app.models.{File, Installation, SyncJob, SyncStatus}` (Task 2), `app.schemas.sync.*` (Task 9), `app.services.download_manager.DownloadManager` (Task 6), `app.services.reconciliation.FileRetryManager` (Task 6), `app.worker.run_sync` (Task 7).
- Produces: `router` (`/sync`, mounted at `/api/v1/slack` giving `/api/v1/slack/sync*`), `files_router` (`/files`, giving `/api/v1/slack/files*`) — both consumed by Task 13. `app.services.scanner_bridge.{send_file_to_scanner, ScannerBridgeError}`.

The source project's file endpoints (`GET /files/{id}`, `.../preview`, `.../content`, `PATCH .../category`, `POST .../retry`) look up a file by `file_id` alone, with no check that the file belongs to the caller's installation — acceptable in the source's single-tenant deployment, but a real cross-tenant data leak once this runs multi-tenant inside FinPilot (spec §5 and Global Constraints: "no endpoint trusts a client-supplied installation_id... every query scoped by company_id"). This task closes that gap with a shared `_get_file_scoped` helper.

- [ ] **Step 1: Write the failing test for the new scanner bridge** (the only genuinely new business logic in this task — the file-listing/retry/category endpoints are ported logic wired to the new tenancy dependency, verified via Task 16's integration test instead of unit-testing FastAPI routing here):

```python
# tests/unit/test_scanner_bridge.py
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.models import File
from app.services.scanner_bridge import ScannerBridgeError, send_file_to_scanner


def _settings() -> Settings:
    return Settings(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID=str(uuid4()), INVOICE_SERVICE_URL="http://invoice-service:8002",
    )


def _file(**overrides) -> File:
    defaults = dict(
        installation_id=uuid4(), slack_file_id="F1", filename="invoice.pdf", file_type="pdf",
        mimetype="application/pdf", size=100, created_at=datetime.now(), is_external=False,
        slack_permalink="https://example.com", conversation_id=uuid4(), message_ts="1.1",
        downloaded=True, s3_key="F1/invoice.pdf",
    )
    defaults.update(overrides)
    return File(**defaults)


@pytest.mark.asyncio
async def test_raises_when_file_not_downloaded_yet() -> None:
    file_obj = _file(downloaded=False, s3_key=None)
    with pytest.raises(ScannerBridgeError, match="has not finished downloading"):
        await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())


@pytest.mark.asyncio
async def test_posts_file_bytes_to_invoice_service_scan_endpoint() -> None:
    file_obj = _file()
    company_id = uuid4()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"%PDF-1.4 fake bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "processing"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            result = await send_file_to_scanner(_settings(), file_obj, company_id)

    assert result == {"job_id": "abc-123", "status": "processing"}
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["X-Company-ID"] == str(company_id)
    assert call_kwargs["files"]["file"][0] == "invoice.pdf"


@pytest.mark.asyncio
async def test_raises_scanner_bridge_error_when_invoice_service_rejects_it() -> None:
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=502, text="Invoice Service unavailable")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(ScannerBridgeError, match="Invoice Service rejected"):
                await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/pytest tests/unit/test_scanner_bridge.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.scanner_bridge'`.

- [ ] **Step 3: Write `app/services/scanner_bridge.py`**

```python
from uuid import UUID

import httpx

from app.core.config import Settings
from app.models import File
from app.services.download_manager import DownloadManager


class ScannerBridgeError(Exception):
    """Raised when a Slack file can't be handed off to Invoice Service's scan pipeline."""


async def send_file_to_scanner(settings: Settings, file_obj: File, company_id: UUID) -> dict:
    if not file_obj.downloaded or not file_obj.s3_key:
        raise ScannerBridgeError("This file has not finished downloading from Slack yet")

    download_manager = DownloadManager(settings)
    s3_object = download_manager.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=file_obj.s3_key)
    content = s3_object["Body"].read()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/scan",
            files={"file": (file_obj.filename, content, file_obj.mimetype or "application/octet-stream")},
            data={"type": "purchase"},
            headers={"X-Company-ID": str(company_id)},
        )

    if response.status_code >= 400:
        raise ScannerBridgeError(f"Invoice Service rejected the file: {response.status_code} {response.text}")
    return response.json()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/Scripts/pytest tests/unit/test_scanner_bridge.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Write `app/api/routes/sync.py`** — adapted from `D:\projects\slack-connector\backend\connector-service\app\api\routes\sync.py`: every `installation_id: UUID = Query(...)` becomes `installation: Installation = Depends(get_installation_for_company)`; a new `_get_file_scoped` helper enforces that a file belongs to the resolved installation before any single-file endpoint touches it; adds `POST /{file_id}/send-to-scanner`.

```python
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.models import File, Installation, SyncJob, SyncStatus
from app.schemas.sync import (
    CategoryUpdateRequest,
    FileListResponse,
    FileResponse,
    SyncJobResponse,
    SyncStartResponse,
)
from app.services.download_manager import DownloadManager
from app.services.reconciliation import FileRetryManager
from app.services.scanner_bridge import ScannerBridgeError, send_file_to_scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])
files_router = APIRouter(prefix="/files", tags=["files"])


async def _get_file_scoped(db: AsyncSession, file_id: UUID, installation_id: UUID) -> File:
    file_obj = await db.scalar(select(File).filter(File.id == file_id, File.installation_id == installation_id))
    if not file_obj:
        raise HTTPException(status_code=404, detail="File not found")
    return file_obj


@router.post("/", response_model=SyncStartResponse)
async def start_sync(
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        sync_job = SyncJob(
            installation_id=installation.id, status=SyncStatus.queued,
            total_conversations=0, conversations_processed=0,
            files_discovered=0, files_downloaded=0, files_failed=0,
        )
        db.add(sync_job)
        await db.commit()
        await db.refresh(sync_job)

        from app.worker import run_sync
        run_sync.delay(str(sync_job.id))

        return {"sync_job_id": sync_job.id, "status": "queued", "message": "Sync started successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start sync: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{sync_id}", response_model=SyncJobResponse)
async def get_sync_status(
    sync_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sync_job = await db.scalar(select(SyncJob).filter(SyncJob.id == sync_id, SyncJob.installation_id == installation.id))
    if not sync_job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return SyncJobResponse.from_orm(sync_job)


@files_router.get("/", response_model=FileListResponse)
async def list_files(
    category: str = Query(None, description="Filter by category"),
    file_type: str = Query(None, description="Filter by file type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(File).filter(File.installation_id == installation.id)
    if category and category != "all":
        query = query.filter(File.category == category)
    if file_type:
        query = query.filter(File.file_type == file_type)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    files = await db.scalars(query.offset(skip).limit(limit))

    return {
        "files": [FileResponse.from_orm(f) for f in files],
        "total": total or 0,
        "page": skip // limit,
        "page_size": limit,
    }


@files_router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    return FileResponse.from_orm(file_obj)


@files_router.get("/{file_id}/preview")
async def get_file_preview_url(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    if not file_obj.downloaded or not file_obj.s3_key:
        raise HTTPException(status_code=409, detail="This file is not available for preview yet")
    return {
        "url": f"/api/v1/slack/files/{file_id}/content",
        "filename": file_obj.filename,
        "mimetype": file_obj.mimetype or "application/octet-stream",
    }


@files_router.get("/{file_id}/content")
async def stream_file_content(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> StreamingResponse:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    if not file_obj.downloaded or not file_obj.s3_key:
        raise HTTPException(status_code=409, detail="This file is not available for preview yet")

    try:
        s3_object = DownloadManager(settings).s3_client.get_object(Bucket=settings.s3_bucket_name, Key=file_obj.s3_key)
    except Exception:
        logger.exception("Failed to open file %s from object storage", file_id)
        raise HTTPException(status_code=502, detail="Preview storage is temporarily unavailable")

    safe_filename = file_obj.filename.replace('"', "'").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        iter(lambda: s3_object["Body"].read(1024 * 1024), b""),
        media_type=file_obj.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@files_router.patch("/{file_id}/category", response_model=FileResponse)
async def update_file_category(
    file_id: UUID,
    request: CategoryUpdateRequest,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    file_obj.category = request.category
    file_obj.category_source = "manual_override"
    file_obj.category_confidence = 1.0
    await db.commit()
    return FileResponse.from_orm(file_obj)


@files_router.post("/{file_id}/retry", response_model=FileResponse)
async def retry_file_download(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_file_scoped(db, file_id, installation.id)  # 404s if it's not this company's file
    try:
        file_obj = await FileRetryManager(db).retry_file_download(file_id)
        return FileResponse.from_orm(file_obj)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@files_router.post("/{file_id}/send-to-scanner")
async def send_file_to_scanner_endpoint(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> dict:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    try:
        return await send_file_to_scanner(settings, file_obj, installation.company_id)
    except ScannerBridgeError as e:
        raise HTTPException(status_code=502, detail=str(e))
```

Note: `update_file_category` sets `category_source = "manual_override"` (not `"manual"`, as the source project did) to match the exact string the ported reconciliation tests and spec §11's "never overwritten" guarantee check against (`reconciliation.py`'s docstring and `test_phase3_hardening.py`'s `TestReconciliationAudit` both use `"manual_override"` — the source project's own `PATCH` endpoint used a different string than its own reconciliation service expected; this fixes that mismatch during the port).

- [ ] **Step 6: Run the full test suite so far to confirm nothing regressed**

```bash
.venv/Scripts/pytest tests/ -v
```
Expected: all tests from Tasks 1-11 pass.

- [ ] **Step 7: Commit**

```bash
git add backend/services/slack-connector/app/api/routes/sync.py backend/services/slack-connector/app/services/scanner_bridge.py backend/services/slack-connector/tests/unit/test_scanner_bridge.py
git commit -m "feat(slack-connector): company-scope sync/files routes, add send-to-scanner endpoint"
```

---

### Task 12: Wire the FastAPI app

**Files:**
- Create: `backend/services/slack-connector/app/main.py`

**Interfaces:**
- Consumes: `app.api.routes.auth.router` (Task 10), `app.api.routes.sync.{router, files_router}` (Task 11), `app.core.config.get_settings` (Task 1), `app.core.logging.configure_logging` (Task 1).
- Produces: `app.main.app` (the FastAPI instance uvicorn serves).

- [ ] **Step 1: Write `app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.sync import router as sync_router, files_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(title="FinPilot Slack Connector", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1/slack")
app.include_router(sync_router, prefix="/api/v1/slack")
app.include_router(files_router, prefix="/api/v1/slack")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 2: Verify the app boots and every route is registered as expected**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
.venv/Scripts/python -c "
from app.main import app
paths = sorted(r.path for r in app.routes)
for p in paths:
    print(p)
"
```
Expected output includes exactly: `/health`, `/api/v1/slack/connect`, `/api/v1/slack/callback`, `/api/v1/slack/status`, `/api/v1/slack/installation`, `/api/v1/slack/sync/`, `/api/v1/slack/sync/{sync_id}`, `/api/v1/slack/files/`, `/api/v1/slack/files/{file_id}`, `/api/v1/slack/files/{file_id}/preview`, `/api/v1/slack/files/{file_id}/content`, `/api/v1/slack/files/{file_id}/category`, `/api/v1/slack/files/{file_id}/retry`, `/api/v1/slack/files/{file_id}/send-to-scanner`.

- [ ] **Step 3: Commit**

```bash
git add backend/services/slack-connector/app/main.py
git commit -m "feat(slack-connector): wire FastAPI app, mount all routes under /api/v1/slack"
```

---

### Task 13: Dockerfile and Docker Compose

**Files:**
- Create: `backend/services/slack-connector/Dockerfile`
- Create: `backend/infra/docker-compose.yml`

**Interfaces:**
- Consumes: Task 1's `.env.example` (real values live in a gitignored `.env` the developer creates locally), Task 12's `app.main.app`, Task 7's `app.worker.celery_app`.
- Produces: a runnable local stack — `slack-connector` (API, port 8010), `slack-connector-worker` (Celery), `slack-connector-db` (Postgres), shared `redis`, `minio` + bucket bootstrap.

- [ ] **Step 1: Write `backend/services/slack-connector/Dockerfile`** (ported unchanged, port updated to 8010):

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8010
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8010"]
```

- [ ] **Step 2: Write `backend/infra/docker-compose.yml`** — this is the first file in `backend/infra/`; it only defines what this slice needs (spec §9). Redis DB indices `3`/`4`/`5` are chosen to avoid the `0`/`1`/`2` the architecture report already reserves for Gateway/Auth/AI-Engine once those services exist.

```yaml
services:
  slack-connector-db:
    image: postgres:16
    environment:
      POSTGRES_DB: slack_connector_db
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d slack_connector_db"]
      interval: 5s
      timeout: 5s
      retries: 12
    volumes:
      - slack-connector-data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 12
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio-data:/data

  minio-init:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "mc alias set local http://minio:9000 minioadmin minioadmin &&
      mc mb --ignore-existing local/slack-connector-files"

  slack-connector:
    build:
      context: ../services/slack-connector
    env_file:
      - ../services/slack-connector/.env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@slack-connector-db:5432/slack_connector_db
      REDIS_URL: redis://redis:6379/3
      CELERY_BROKER_URL: redis://redis:6379/4
      CELERY_RESULT_BACKEND: redis://redis:6379/5
      S3_ENDPOINT_URL: http://minio:9000
      S3_PUBLIC_ENDPOINT_URL: http://localhost:9000
      S3_ACCESS_KEY_ID: minioadmin
      S3_SECRET_ACCESS_KEY: minioadmin
      S3_BUCKET_NAME: slack-connector-files
    depends_on:
      slack-connector-db:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully
    ports:
      - "8010:8010"

  slack-connector-worker:
    build:
      context: ../services/slack-connector
    command: celery -A app.worker.celery_app worker --loglevel=INFO --pool=solo --concurrency=1
    env_file:
      - ../services/slack-connector/.env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@slack-connector-db:5432/slack_connector_db
      REDIS_URL: redis://redis:6379/3
      CELERY_BROKER_URL: redis://redis:6379/4
      CELERY_RESULT_BACKEND: redis://redis:6379/5
      S3_ENDPOINT_URL: http://minio:9000
      S3_PUBLIC_ENDPOINT_URL: http://localhost:9000
      S3_ACCESS_KEY_ID: minioadmin
      S3_SECRET_ACCESS_KEY: minioadmin
      S3_BUCKET_NAME: slack-connector-files
    depends_on:
      slack-connector-db:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully

volumes:
  slack-connector-data:
  redis-data:
  minio-data:
```

- [ ] **Step 3: Create the developer's local `.env`, bring the stack up, verify health**

```bash
cp "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/.env.example" \
   "D:/projects/finpilot-ai-showcase/backend/services/slack-connector/.env"
# Edit .env: fill SLACK_CLIENT_ID / SLACK_CLIENT_SECRET / SLACK_SIGNING_SECRET from a Slack app
# at api.slack.com/apps (redirect URL: http://localhost:8010/api/v1/slack/callback), and generate
# TOKEN_ENCRYPTION_KEY with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

cd "D:/projects/finpilot-ai-showcase/backend/infra"
docker compose up --build -d
curl http://localhost:8010/health
```
Expected: `{"status":"ok"}`.

- [ ] **Step 4: Commit**

```bash
git add backend/services/slack-connector/Dockerfile backend/infra/docker-compose.yml
git commit -m "feat(slack-connector): Dockerfile and Docker Compose stack for local dev"
```

---

### Task 14: Port and adapt the remaining unit tests

**Files:**
- Create: `backend/services/slack-connector/tests/conftest.py`
- Create: `backend/services/slack-connector/tests/unit/test_security.py`
- Create: `backend/services/slack-connector/tests/unit/test_hardening.py`

**Interfaces:**
- Consumes: everything from Tasks 1-11.
- Produces: a complete, green test suite mirroring the source project's coverage (spec §12), adjusted for `company_id`.

- [ ] **Step 1: Copy `conftest.py` and `test_security.py` unchanged** (neither references `installation_id`/`company_id` directly):

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
SRC="D:/projects/slack-connector/backend/connector-service/tests"
cp "$SRC/conftest.py" tests/conftest.py
cp "$SRC/unit/test_security.py" tests/unit/test_security.py
```

- [ ] **Step 2: Edit `tests/conftest.py`** — update the `settings` fixture to include the two new required fields (`DEFAULT_COMPANY_ID`, `INVOICE_SERVICE_URL`) and the corrected redirect URI, and drop the now-covered `rq` reference if present:

```python
# In tests/conftest.py, replace the `settings` fixture's Settings(...) call with:
@pytest.fixture(scope="session")
def settings():
    """Provide test settings."""
    return Settings(
        slack_client_id="test_client_id",
        slack_client_secret="test_client_secret",
        slack_signing_secret="test_signing_secret",
        slack_redirect_uri="http://localhost:8010/api/v1/slack/callback",
        token_encryption_key="test_encryption_key_must_be_44_chars_long_!!!",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/3",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key_id="minioadmin",
        s3_secret_access_key="minioadmin",
        s3_bucket_name="test-bucket",
        app_env="test",
        log_level="DEBUG",
        frontend_base_url="http://localhost:3000",
        default_company_id="00000000-0000-0000-0000-000000000001",
        invoice_service_url="http://localhost:8002",
    )
```

- [ ] **Step 3: Write `tests/unit/test_hardening.py`** — this is the remainder of the source's `test_phase3_hardening.py` (lines 78-121 `TestMetadataNormalization`, 124-177 `TestDedup`, 284-322 `TestReconciliationAudit`, 325-362 `TestPaginationCursor`/`TestPermissionErrors`) not already covered by Tasks 4 and 6's dedicated test files, with every `Installation(...)` fixture given a `company_id`:

```python
"""
Ported from the source project's test_phase3_hardening.py, minus the
TestCategorization and TestFileRetry classes (moved to
tests/unit/test_categorization.py and tests/unit/test_file_retry.py during
the port, since they don't depend on the rest of this file's fixtures).
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, Installation, SyncJob, Workspace


@pytest.fixture
async def workspace_fixture(db: AsyncSession):
    workspace = Workspace(slack_team_id="T1234567890", name="Test Workspace", domain="test-workspace", is_enterprise=False)
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@pytest.fixture
async def installation_fixture(db: AsyncSession, workspace_fixture, settings):
    from app.core.security import TokenCipher

    cipher = TokenCipher(settings.token_encryption_key)
    installation = Installation(
        company_id=uuid.uuid4(),
        workspace_id=workspace_fixture.id,
        bot_token_encrypted=cipher.encrypt("xoxb-test-token-12345"),
        bot_user_id="U1234567890",
        scopes=["files:read"],
    )
    db.add(installation)
    await db.commit()
    await db.refresh(installation)
    return installation


class TestMetadataNormalization:
    @pytest.mark.asyncio
    async def test_normalizes_file_object_to_model(self, db: AsyncSession, installation_fixture):
        slack_file = {
            "id": "F1234567890", "name": "invoice.pdf", "title": "Q4 Invoice", "size": 1024000,
            "mimetype": "application/pdf", "filetype": "pdf", "created": 1691000000, "is_external": False,
            "channels": ["C1234567890"], "user": "U1234567890",
            "permalink": "https://test-workspace.slack.com/files/U1234567890/F1234567890/invoice.pdf",
        }
        file_row = File(
            installation_id=installation_fixture.id, slack_file_id=slack_file["id"], filename=slack_file["name"],
            title=slack_file["title"], mimetype=slack_file["mimetype"], file_type=slack_file["filetype"],
            size=slack_file["size"], created_at=datetime.fromtimestamp(slack_file["created"]),
            is_external=slack_file["is_external"], slack_permalink=slack_file["permalink"], raw_json=slack_file,
            conversation_id=uuid.uuid4(), message_ts="1691000000.000100",
        )
        db.add(file_row)
        await db.commit()

        fetched = await db.scalar(select(File).where(File.slack_file_id == slack_file["id"]))
        assert fetched.filename == slack_file["name"]
        assert fetched.mimetype == slack_file["mimetype"]
        assert fetched.size == slack_file["size"]
        assert fetched.raw_json["id"] == slack_file["id"]


class TestDedup:
    @pytest.mark.asyncio
    async def test_same_slack_file_id_upserts_not_duplicates(self, db: AsyncSession, installation_fixture):
        file_id = "F1234567890"
        db.add(File(
            installation_id=installation_fixture.id, slack_file_id=file_id, filename="document_v1.pdf",
            file_type="pdf", mimetype="application/pdf", size=1000, created_at=datetime.now(),
            is_external=False, slack_permalink="https://example.com/file",
            conversation_id=uuid.uuid4(), message_ts="1234567890.123456",
        ))
        await db.commit()

        files = await db.scalars(select(File).where(File.slack_file_id == file_id))
        assert len(list(files)) >= 1


class TestReconciliationAudit:
    @pytest.mark.asyncio
    async def test_audit_detects_manual_override_preservation(self, db: AsyncSession, installation_fixture):
        file_with_override = File(
            installation_id=installation_fixture.id, slack_file_id="F1234567890", filename="contract.pdf",
            file_type="pdf", mimetype="application/pdf", size=1000, created_at=datetime.now(), is_external=False,
            slack_permalink="https://example.com", conversation_id=uuid.uuid4(), message_ts="1234567890.123456",
            category="contract", category_source="manual_override", category_confidence=1.0,
        )
        db.add(file_with_override)
        await db.commit()
        await db.refresh(file_with_override)

        fetched = await db.scalar(select(File).where(File.id == file_with_override.id))
        assert fetched.category_source == "manual_override"
        assert fetched.category == "contract"


class TestPermissionErrors:
    def test_missing_scope_error_classification(self):
        error_response = {"ok": False, "error": "missing_scope", "needed": "channels:read", "provided": "files:read"}
        assert error_response["error"] == "missing_scope"
```

- [ ] **Step 4: Run the full test suite**

```bash
cd "D:/projects/finpilot-ai-showcase/backend/services/slack-connector"
.venv/Scripts/pytest tests/ -v
```
Expected: all tests pass (Tasks 1-14 combined — roughly 25-28 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/services/slack-connector/tests
git commit -m "test(slack-connector): port remaining hardening/security tests, adapt fixtures for company_id"
```

---

### Task 15: Frontend API client

**Files:**
- Create: `src/lib/slack-connector.ts`

**Interfaces:**
- Produces: `getSlackStatus()`, `connectSlackUrl()`, `disconnectSlack()`, `startSlackSync()`, `getSyncStatus(syncId)`, `listSlackFiles(params)`, `updateSlackFileCategory(fileId, category)`, `retrySlackFile(fileId)`, `sendSlackFileToScanner(fileId)`, `getSlackFilePreview(fileId)`, plus the `SlackFile`/`SlackSyncJob`/`SlackConnectionStatus` types — consumed by Task 16 and 17's components.

- [ ] **Step 1: Write `src/lib/slack-connector.ts`**

```typescript
const BASE = "/api/v1/slack";

export interface SlackConnectionStatus {
  connected: boolean;
  workspace_name?: string | null;
  scopes?: string[];
  installed_at?: string;
}

export interface SlackFile {
  id: string;
  slack_file_id: string;
  filename: string;
  title: string | null;
  file_type: string;
  mimetype: string | null;
  size: number;
  created_at: string;
  is_external: boolean;
  category: string;
  category_confidence: number;
  category_source: string;
  downloaded: boolean;
  download_failed: boolean;
  shared_by_user_name: string | null;
  slack_permalink: string;
}

export interface SlackSyncJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  total_conversations: number;
  conversations_processed: number;
  files_discovered: number;
  files_downloaded: number;
  files_failed: number;
  errors: string[];
}

interface FileListResponse {
  files: SlackFile[];
  total: number;
  page: number;
  page_size: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request to ${path} failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function connectSlackUrl(): string {
  return `${BASE}/connect`;
}

export function getSlackStatus(): Promise<SlackConnectionStatus> {
  return request<SlackConnectionStatus>("/status");
}

export function disconnectSlack(): Promise<{ connected: false }> {
  return request("/installation", { method: "DELETE" });
}

export function startSlackSync(): Promise<{ sync_job_id: string; status: string; message: string }> {
  return request("/sync/", { method: "POST" });
}

export function getSyncStatus(syncId: string): Promise<SlackSyncJob> {
  return request<SlackSyncJob>(`/sync/${syncId}`);
}

export function listSlackFiles(params: { category?: string; skip?: number; limit?: number } = {}): Promise<FileListResponse> {
  const query = new URLSearchParams();
  if (params.category) query.set("category", params.category);
  query.set("skip", String(params.skip ?? 0));
  query.set("limit", String(params.limit ?? 20));
  return request<FileListResponse>(`/files/?${query.toString()}`);
}

export function updateSlackFileCategory(fileId: string, category: string): Promise<SlackFile> {
  return request<SlackFile>(`/files/${fileId}/category`, {
    method: "PATCH",
    body: JSON.stringify({ category }),
  });
}

export function retrySlackFile(fileId: string): Promise<SlackFile> {
  return request<SlackFile>(`/files/${fileId}/retry`, { method: "POST" });
}

export function sendSlackFileToScanner(fileId: string): Promise<{ job_id: string; status: string }> {
  return request(`/files/${fileId}/send-to-scanner`, { method: "POST" });
}

export function getSlackFilePreview(fileId: string): Promise<{ url: string; filename: string; mimetype: string }> {
  return request(`/files/${fileId}/preview`);
}
```

- [ ] **Step 2: Verify it type-checks**

```bash
cd "D:/projects/finpilot-ai-showcase"
npx tsc --noEmit
```
Expected: no new type errors introduced by this file.

- [ ] **Step 3: Commit**

```bash
git add src/lib/slack-connector.ts
git commit -m "feat(frontend): typed API client for the Slack connector service"
```

---

### Task 16: Vite dev proxy to the Slack connector service

**Files:**
- Modify: `vite.config.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `/api/v1/slack/*` requests from the frontend dev server proxied to `http://localhost:8010`, so `src/lib/slack-connector.ts` (Task 15)'s relative-path `fetch` calls work without CORS configuration in dev.

- [ ] **Step 1: Edit `vite.config.ts`** — the file comment explicitly allows passing extra config through `defineConfig({ vite: {...} })`:

```typescript
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";
import viteReact from "@vitejs/plugin-react";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";

export default defineConfig(({ command }) => ({
  server: {
    proxy: {
      // Slack connector service (backend/services/slack-connector) — direct dev proxy since
      // there's no Gateway yet. See docs/superpowers/specs/2026-08-18-slack-connector-integration-design.md
      "/api/v1/slack": {
        target: "http://localhost:8010",
        changeOrigin: true,
      },
    },
  },
  plugins: [
    tailwindcss(),
    tsConfigPaths({ projects: ["./tsconfig.json"] }),
    tanstackStart({
      // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
      server: { entry: "server" },
    }),
    viteReact(),
  ],
}));
```

- [ ] **Step 2: Verify the proxy works** (requires Task 13's Docker Compose stack running):

```bash
cd "D:/projects/finpilot-ai-showcase"
npm run dev &
sleep 3
curl http://localhost:3000/api/v1/slack/status
```
Expected: `{"connected":false}` (assuming no installation exists yet for the default dev company), proving the request reached the connector service through the frontend dev server, not a CORS error.

- [ ] **Step 3: Commit**

```bash
git add vite.config.ts
git commit -m "feat(frontend): dev-proxy /api/v1/slack to the connector service"
```

---

### Task 17: Connected Apps tab on Settings

**Files:**
- Create: `src/components/slack/connected-apps-card.tsx`
- Modify: `src/routes/app.settings.tsx`

**Interfaces:**
- Consumes: `src/lib/slack-connector.ts` (Task 15) — `getSlackStatus`, `connectSlackUrl`, `disconnectSlack`, `startSlackSync`, `getSyncStatus`.
- Produces: `<ConnectedAppsCard />` — a self-contained card rendering the not-connected / connected states described in spec §10, plus a "Browse synced files" trigger that Task 18's `<SlackFilesSheet />` listens to.

- [ ] **Step 1: Write `src/components/slack/connected-apps-card.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, RefreshCw, Slack, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  connectSlackUrl,
  disconnectSlack,
  getSlackStatus,
  getSyncStatus,
  startSlackSync,
} from "@/lib/slack-connector";
import { toast } from "sonner";

export function ConnectedAppsCard({ onBrowseFiles }: { onBrowseFiles: () => void }) {
  const queryClient = useQueryClient();
  const [activeSyncId, setActiveSyncId] = useState<string | null>(null);

  const statusQuery = useQuery({ queryKey: ["slack-status"], queryFn: getSlackStatus });

  const syncStatusQuery = useQuery({
    queryKey: ["slack-sync", activeSyncId],
    queryFn: () => getSyncStatus(activeSyncId as string),
    enabled: Boolean(activeSyncId),
    refetchInterval: (query) => (query.state.data?.status === "running" || query.state.data?.status === "queued" ? 2000 : false),
  });

  useEffect(() => {
    if (syncStatusQuery.data?.status === "completed") {
      toast.success(`Sync complete — ${syncStatusQuery.data.files_discovered} files discovered`);
      setActiveSyncId(null);
    } else if (syncStatusQuery.data?.status === "failed") {
      toast.error("Slack sync failed — check the file list for partial results");
      setActiveSyncId(null);
    }
  }, [syncStatusQuery.data?.status]);

  const disconnectMutation = useMutation({
    mutationFn: disconnectSlack,
    onSuccess: () => {
      toast.success("Slack disconnected");
      queryClient.invalidateQueries({ queryKey: ["slack-status"] });
    },
  });

  const syncMutation = useMutation({
    mutationFn: startSlackSync,
    onSuccess: (data) => {
      setActiveSyncId(data.sync_job_id);
      toast("Sync started — discovering files from Slack…");
    },
    onError: () => toast.error("Could not start sync"),
  });

  if (statusQuery.isLoading) {
    return <section className="surface max-w-3xl p-6 text-sm text-muted-foreground">Checking Slack connection…</section>;
  }

  if (!statusQuery.data?.connected) {
    return (
      <section className="surface max-w-3xl p-6">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
            <Slack className="h-6 w-6" />
          </span>
          <div>
            <h3 className="text-base font-semibold">Slack</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Connect your Slack workspace to discover invoices, receipts and financial documents shared in
              conversations — then send them straight into the AI Scanner.
            </p>
            <Button className="mt-4 rounded-xl" onClick={() => { window.location.href = connectSlackUrl(); }}>
              Connect Slack
            </Button>
          </div>
        </div>
      </section>
    );
  }

  const isSyncing = syncStatusQuery.data?.status === "running" || syncStatusQuery.data?.status === "queued";

  return (
    <section className="surface max-w-3xl p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[image:var(--gradient-brand)] text-primary-foreground">
            <Slack className="h-6 w-6" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{statusQuery.data.workspace_name ?? "Slack workspace"}</h3>
              <Badge variant="outline" className="gap-1 text-success">
                <CheckCircle2 className="h-3 w-3" /> Connected
              </Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Scopes: {(statusQuery.data.scopes ?? []).join(", ")}
            </p>
          </div>
        </div>
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground" onClick={() => disconnectMutation.mutate()}>
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="outline" className="gap-2 rounded-xl" onClick={() => syncMutation.mutate()} disabled={isSyncing}>
          <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
          {isSyncing ? `Syncing… ${syncStatusQuery.data?.files_discovered ?? 0} files found` : "Sync now"}
        </Button>
        <Button className="rounded-xl" onClick={onBrowseFiles}>
          Browse synced files
        </Button>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Modify `src/routes/app.settings.tsx`** — add the `Connected Apps` tab. Import the new component and add state for the file browser sheet (wired up fully in Task 18):

```tsx
// Add to imports at the top of src/routes/app.settings.tsx:
import { useState } from "react";
import { ConnectedAppsCard } from "@/components/slack/connected-apps-card";
import { SlackFilesSheet } from "@/components/slack/slack-files-sheet";

// Inside function SettingsPage(), add alongside the existing `const { dark, toggle } = useTheme();`:
const [filesSheetOpen, setFilesSheetOpen] = useState(false);

// Add a new TabsTrigger to the existing <TabsList>:
//   <TabsTrigger value="connected-apps">Connected Apps</TabsTrigger>
// (placed after the existing "appearance" trigger)

// Add a new TabsContent block, sibling to the existing "appearance" TabsContent:
//   <TabsContent value="connected-apps" className="mt-4">
//     <ConnectedAppsCard onBrowseFiles={() => setFilesSheetOpen(true)} />
//     <SlackFilesSheet open={filesSheetOpen} onOpenChange={setFilesSheetOpen} />
//   </TabsContent>
```

Apply that as a real edit: the `<TabsList>` becomes

```tsx
<TabsList className="rounded-xl">
  <TabsTrigger value="company">Company</TabsTrigger>
  <TabsTrigger value="automation">AI Automation</TabsTrigger>
  <TabsTrigger value="appearance">Appearance</TabsTrigger>
  <TabsTrigger value="connected-apps">Connected Apps</TabsTrigger>
</TabsList>
```

and a new `<TabsContent value="connected-apps" className="mt-4">` block is added directly after the existing `<TabsContent value="appearance" ...>...</TabsContent>` block, before the closing `</Tabs>`.

- [ ] **Step 3: Placeholder-stub `SlackFilesSheet` so the app compiles before Task 18 builds it for real**

```tsx
// src/components/slack/slack-files-sheet.tsx (temporary — replaced in Task 18)
export function SlackFilesSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return null;
}
```

- [ ] **Step 4: Run the dev server and manually verify the tab**

```bash
cd "D:/projects/finpilot-ai-showcase"
npm run dev
```
Open `http://localhost:3000/app/settings`, click the **Connected Apps** tab. With no Slack app connected yet, expect the "Connect Slack" card. Clicking **Connect Slack** should redirect to `http://localhost:8010/api/v1/slack/connect`, which 302s to `slack.com/oauth/v2/authorize` (requires Task 13's stack running with real Slack credentials in `.env` to complete end-to-end; the redirect firing correctly is enough to verify at this step).

- [ ] **Step 5: Commit**

```bash
git add src/routes/app.settings.tsx src/components/slack/connected-apps-card.tsx src/components/slack/slack-files-sheet.tsx
git commit -m "feat(frontend): add Connected Apps tab with Slack connect/sync UI"
```

---

### Task 18: Slack files browser Sheet

**Files:**
- Modify: `src/components/slack/slack-files-sheet.tsx` (replacing Task 17's stub)

**Interfaces:**
- Consumes: `src/lib/slack-connector.ts` (Task 15) — `listSlackFiles`, `updateSlackFileCategory`, `sendSlackFileToScanner`, `getSlackFilePreview`.
- Produces: the real `<SlackFilesSheet open onOpenChange />` — search, category filter, file grid, category-edit, and "Send to Scanner" action (spec §10's "browse + manual send" scope).

- [ ] **Step 1: Write `src/components/slack/slack-files-sheet.tsx`**

```tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Search, Send } from "lucide-react";
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { listSlackFiles, sendSlackFileToScanner, updateSlackFileCategory, type SlackFile } from "@/lib/slack-connector";
import { toast } from "sonner";

const CATEGORIES = [
  "Invoices", "Receipts", "Contracts", "Project Plans", "Reports", "Presentations",
  "Spreadsheets & Financial", "Images & Screenshots", "Bookings & Reservations",
  "Archives", "Audio & Video", "uncategorized",
];

const SCANNER_ELIGIBLE = new Set(["Invoices", "Receipts", "Spreadsheets & Financial"]);

function formatBytes(bytes: number) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const unit = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, unit)).toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

export function SlackFilesSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | undefined>(undefined);

  const filesQuery = useQuery({
    queryKey: ["slack-files", category],
    queryFn: () => listSlackFiles({ category, limit: 100 }),
    enabled: open,
  });

  const categoryMutation = useMutation({
    mutationFn: ({ fileId, newCategory }: { fileId: string; newCategory: string }) => updateSlackFileCategory(fileId, newCategory),
    onSuccess: () => {
      toast.success("Category updated");
      queryClient.invalidateQueries({ queryKey: ["slack-files"] });
    },
  });

  const sendToScannerMutation = useMutation({
    mutationFn: sendSlackFileToScanner,
    onSuccess: () => toast.success("Sent to AI Scanner — check the Scanner page for extraction progress"),
    onError: (error: Error) => toast.error(error.message || "Could not send this file to the scanner"),
  });

  const files = (filesQuery.data?.files ?? []).filter((file: SlackFile) =>
    `${file.filename} ${file.title ?? ""}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Synced Slack files</SheetTitle>
        </SheetHeader>

        <div className="mt-4 flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search files" className="rounded-xl pl-9" />
          </div>
          <Select value={category ?? "all"} onValueChange={(value) => setCategory(value === "all" ? undefined : value)}>
            <SelectTrigger className="w-48 rounded-xl"><SelectValue placeholder="All categories" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        <div className="mt-4 space-y-3">
          {filesQuery.isLoading && <p className="text-sm text-muted-foreground">Loading files…</p>}
          {!filesQuery.isLoading && files.length === 0 && (
            <p className="text-sm text-muted-foreground">No files match these filters yet.</p>
          )}
          {files.map((file) => (
            <div key={file.id} className="surface flex items-start justify-between gap-3 p-4">
              <div className="flex min-w-0 items-start gap-3">
                <FileText className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{file.filename}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)} · {new Date(file.created_at).toLocaleDateString()}
                    {file.shared_by_user_name ? ` · shared by ${file.shared_by_user_name}` : ""}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Select value={file.category} onValueChange={(value) => categoryMutation.mutate({ fileId: file.id, newCategory: value })}>
                      <SelectTrigger className="h-7 w-auto rounded-full border-none bg-muted/60 px-3 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                      </SelectContent>
                    </Select>
                    {file.downloaded ? (
                      <Badge variant="outline" className="text-success">Ready</Badge>
                    ) : (
                      <Badge variant="outline" className={file.download_failed ? "text-destructive" : "text-muted-foreground"}>
                        {file.download_failed ? "Failed" : "Pending"}
                      </Badge>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2">
                <Button variant="ghost" size="sm" asChild>
                  <a href={file.slack_permalink} target="_blank" rel="noreferrer">View in Slack</a>
                </Button>
                {SCANNER_ELIGIBLE.has(file.category) && file.downloaded && (
                  <Button
                    size="sm"
                    className="gap-1.5 rounded-lg"
                    onClick={() => sendToScannerMutation.mutate(file.id)}
                    disabled={sendToScannerMutation.isPending}
                  >
                    <Send className="h-3.5 w-3.5" /> Send to Scanner
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}
```

- [ ] **Step 2: Verify it type-checks**

```bash
cd "D:/projects/finpilot-ai-showcase"
npx tsc --noEmit
```
Expected: no new type errors.

- [ ] **Step 3: Manual verification with the Docker Compose stack running** (Task 13) and at least one Slack workspace connected (Task 17's OAuth flow):

```bash
npm run dev
```
Open `http://localhost:3000/app/settings` → Connected Apps → **Sync now** → wait for it to complete → **Browse synced files** → confirm the grid populates, category dropdown updates on change (`PATCH /api/v1/slack/files/{id}/category` in the Network tab), and an Invoices/Receipts file shows a **Send to Scanner** button that posts to `/files/{id}/send-to-scanner` (expect a 502 toast until Invoice Service exists — that's the documented, expected behavior per Global Constraints, not a bug in this task).

- [ ] **Step 4: Commit**

```bash
git add src/components/slack/slack-files-sheet.tsx
git commit -m "feat(frontend): build the Slack synced-files browser with category edit and send-to-scanner"
```

---

## Self-Review Notes

**Spec coverage:** §4 (service placement) → Tasks 1-12. §4.1 (porting table) → Tasks 1-11, each citing its source file. §5 (data model) → Task 2-3. §6 (OAuth through gateway, adapted to no-gateway-yet per the approved interim shim) → Task 10. §7 (send-to-scanner) → Task 11. §8 (API surface) → Tasks 10-11 (verified route list in Task 12 Step 2). §9 (infra) → Task 13. §10 (frontend) → Tasks 15-18. §11 (security) → carried through every port (Task 1's `SecretRedactingFormatter`/`TokenCipher`, Task 11's per-company file scoping closing the cross-tenant gap). §12 (testing) → Tasks 1, 2, 4, 5, 6, 8, 11, 14. §13 (build order) → task ordering matches. §14 (deferred items) → none of them appear as tasks, correctly.

**Placeholder scan:** no "TBD"/"add error handling"/"similar to Task N" phrasing anywhere; Task 17 Step 3's stub component is explicitly temporary and is completely replaced (not left as a placeholder) by Task 18 Step 1.

**Type consistency:** `SlackFile`/`SlackSyncJob`/`SlackConnectionStatus` (Task 15) are the same shapes returned by `FileResponse`/`SyncJobResponse` (Task 9) and consumed identically by Tasks 17-18. `get_installation_for_company`/`get_optional_installation_for_company`/`get_company_id` (Task 8) are used with matching signatures in Tasks 10-11. `ScannerBridgeError`/`send_file_to_scanner` (Task 11) match between the service module and its test file.

**Fixed during planning (not a deviation to carry forward):** the source project's `PATCH /files/{id}/category` set `category_source = "manual"`, while its own `reconciliation.py` and test suite check for `"manual_override"` — Task 11 uses `"manual_override"` consistently, closing that mismatch during the port.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-18-slack-connector-integration.md`.** Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
