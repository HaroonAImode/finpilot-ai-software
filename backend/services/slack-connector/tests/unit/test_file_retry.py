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
