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
