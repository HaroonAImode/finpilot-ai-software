"""SyncJob.errors must survive a commit.

Found during a real sync: two conversations failed with genuinely useful Slack
errors (not_in_channel on a private channel, channel_not_found on a DM), the
orchestrator appended both to sync_job.errors — and the API still returned
errors: []. SyncJob.errors was a plain ARRAY column, and SQLAlchemy does not
detect in-place list mutation, so every append was discarded at flush time.

The user-visible effect was a sync reporting "8/10 conversations" with no
explanation and no hint that the fix is to invite the bot to the channel.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Installation, SyncJob, Workspace
from app.models.base import Base


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def installation(db: AsyncSession):
    workspace = Workspace(slack_team_id="T1", name="Test", domain="test", is_enterprise=False)
    db.add(workspace)
    await db.flush()
    inst = Installation(
        company_id=uuid.uuid4(),
        workspace_id=workspace.id,
        bot_token_encrypted="enc",
        bot_user_id="U1",
        scopes=["files:read"],
    )
    db.add(inst)
    await db.commit()
    return inst


async def _reload(db: AsyncSession, job_id) -> SyncJob:
    """Re-read from the database rather than trusting the in-memory object."""
    db.expunge_all()
    return await db.scalar(select(SyncJob).filter(SyncJob.id == job_id))


@pytest.mark.asyncio
async def test_appended_error_survives_commit(db, installation) -> None:
    job = SyncJob(installation_id=installation.id)
    db.add(job)
    await db.commit()

    job.errors.append("Conversation error: Slack API error: not_in_channel")
    await db.commit()

    assert (await _reload(db, job.id)).errors == [
        "Conversation error: Slack API error: not_in_channel"
    ]


@pytest.mark.asyncio
async def test_multiple_appends_all_survive(db, installation) -> None:
    job = SyncJob(installation_id=installation.id)
    db.add(job)
    await db.commit()

    job.errors.append("Conversation error: not_in_channel")
    job.errors.append("Conversation error: channel_not_found")
    await db.commit()

    reloaded = await _reload(db, job.id)
    assert len(reloaded.errors) == 2
    assert "channel_not_found" in reloaded.errors[1]


@pytest.mark.asyncio
async def test_errors_defaults_to_empty_list(db, installation) -> None:
    job = SyncJob(installation_id=installation.id)
    db.add(job)
    await db.commit()

    assert (await _reload(db, job.id)).errors == []
