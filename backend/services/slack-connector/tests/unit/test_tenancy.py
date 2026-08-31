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
    result = await get_company_id(authorization=None, x_company_id=None, settings=_settings(str(default_id)))
    assert result == default_id


@pytest.mark.asyncio
async def test_get_company_id_prefers_explicit_header() -> None:
    header_id = uuid.uuid4()
    result = await get_company_id(
        authorization=None, x_company_id=str(header_id), settings=_settings(str(uuid.uuid4()))
    )
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
