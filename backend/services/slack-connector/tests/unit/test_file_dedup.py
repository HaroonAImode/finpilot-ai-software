"""Re-syncing must update existing file rows, not duplicate them.

Found by running two real syncs: 23 Slack files produced 46 File rows, every one
listed twice in the browser. persist_file always INSERTed, unlike the
get_or_create_* helpers beside it.

The pre-existing TestDedup case asserted `len(files) >= 1`, which is true whether
there is one row or a hundred — it passed throughout. These assert exact counts.
"""
import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import File, Installation, Workspace
from app.models.base import Base
from app.services.slack.discovery import DiscoveryPersistenceService

SLACK_FILE_ID = "F0BQRLYGWKS"


def _record(**overrides):
    record = {
        "slack_file_id": SLACK_FILE_ID,
        "message_ts": "1691000000.000100",
        "thread_ts": None,
        "filename": "invoice.pdf",
        "title": "Invoice",
        "file_type": "PDF",
        "mimetype": "application/pdf",
        "size": 1024,
        "created_at": 1691000000,
        "is_external": False,
        "external_type": None,
        "url_private_download": "https://files.slack.com/x",
        "shared_by_user_name": "haroon",
        "slack_permalink": "https://slack.com/files/x",
        "raw_json": {"id": SLACK_FILE_ID},
    }
    record.update(overrides)
    return record


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


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(File))


@pytest.mark.asyncio
async def test_resyncing_same_file_updates_instead_of_duplicating(db, installation) -> None:
    service = DiscoveryPersistenceService(db, installation.id)
    conversation_id = uuid.uuid4()

    first = await service.persist_file(_record(), conversation_id, ("Invoices", 0.95, "rule"))
    await db.commit()
    second = await service.persist_file(_record(), conversation_id, ("Invoices", 0.95, "rule"))
    await db.commit()

    assert await _count(db) == 1
    assert first.id == second.id


@pytest.mark.asyncio
async def test_resync_refreshes_changed_metadata(db, installation) -> None:
    service = DiscoveryPersistenceService(db, installation.id)
    conversation_id = uuid.uuid4()

    await service.persist_file(_record(), conversation_id, ("Invoices", 0.95, "rule"))
    await db.commit()
    await service.persist_file(
        _record(filename="invoice-final.pdf", size=2048), conversation_id, ("Invoices", 0.95, "rule")
    )
    await db.commit()

    row = await db.scalar(select(File).filter(File.slack_file_id == SLACK_FILE_ID))
    assert row.filename == "invoice-final.pdf"
    assert row.size == 2048
    assert await _count(db) == 1


@pytest.mark.asyncio
async def test_resync_does_not_revert_a_manual_category_override(db, installation) -> None:
    """The point of manual_override: a later sync must not undo a human's fix."""
    service = DiscoveryPersistenceService(db, installation.id)
    conversation_id = uuid.uuid4()

    row = await service.persist_file(_record(), conversation_id, ("uncategorized", 0.0, "rule"))
    await db.commit()

    # user corrects it in the UI
    row.category = "Receipts"
    row.category_source = "manual_override"
    row.category_confidence = 1.0
    await db.commit()

    # classifier would say "uncategorized" again on the next sync
    await service.persist_file(_record(), conversation_id, ("uncategorized", 0.0, "rule"))
    await db.commit()

    refreshed = await db.scalar(select(File).filter(File.slack_file_id == SLACK_FILE_ID))
    assert refreshed.category == "Receipts"
    assert refreshed.category_source == "manual_override"


@pytest.mark.asyncio
async def test_rule_categories_are_still_refreshed_on_resync(db, installation) -> None:
    service = DiscoveryPersistenceService(db, installation.id)
    conversation_id = uuid.uuid4()

    await service.persist_file(_record(), conversation_id, ("uncategorized", 0.0, "rule"))
    await db.commit()
    await service.persist_file(_record(), conversation_id, ("Invoices", 0.95, "rule"))
    await db.commit()

    row = await db.scalar(select(File).filter(File.slack_file_id == SLACK_FILE_ID))
    assert row.category == "Invoices"


@pytest.mark.asyncio
async def test_different_slack_files_still_create_separate_rows(db, installation) -> None:
    service = DiscoveryPersistenceService(db, installation.id)
    conversation_id = uuid.uuid4()

    await service.persist_file(_record(), conversation_id, ("Invoices", 0.95, "rule"))
    await service.persist_file(
        _record(slack_file_id="F_OTHER", filename="other.pdf"), conversation_id, ("Reports", 0.9, "rule")
    )
    await db.commit()

    assert await _count(db) == 2
