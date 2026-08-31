"""1:1 DM conversations must get a real display name, not "unknown".

Observed live: two DM conversations in a real workspace were both stored as
name="unknown" because Slack's conversations.list response for an `im` never
includes a `name` field, only a `user` id — and the code fell back to the
literal string "unknown" unconditionally. Harmless while nothing displayed
conversation names; actively broken for Phase 3, where a "group files by
conversation" view would show two identical, meaningless section headers.
"""
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ConversationType, Installation, Workspace
from app.models.base import Base
from app.services.slack.discovery import DiscoveryPersistenceService

DM_CONV_DATA = {"id": "D1", "is_im": True, "user": "U_OTHER"}


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
        company_id=uuid.uuid4(), workspace_id=workspace.id,
        bot_token_encrypted="enc", bot_user_id="U1", scopes=["files:read"],
    )
    db.add(inst)
    await db.commit()
    return inst


def _discovery_returning(profile: dict):
    discovery = AsyncMock()
    discovery.enrich_user_info = AsyncMock(return_value=profile)
    return discovery


class TestNewDmConversation:
    @pytest.mark.asyncio
    async def test_dm_gets_the_other_persons_real_name(self, db, installation) -> None:
        service = DiscoveryPersistenceService(db, installation.id)
        discovery = _discovery_returning({"real_name": "Ayesha Khan", "name": "ayesha"})

        conv = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=discovery)

        assert conv.name == "Ayesha Khan"
        assert conv.conversation_type == ConversationType.im

    @pytest.mark.asyncio
    async def test_falls_back_to_slack_username_if_no_real_name(self, db, installation) -> None:
        service = DiscoveryPersistenceService(db, installation.id)
        discovery = _discovery_returning({"real_name": "", "name": "ayesha"})

        conv = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=discovery)

        assert conv.name == "ayesha"

    @pytest.mark.asyncio
    async def test_no_discovery_service_falls_back_to_unknown_without_raising(self, db, installation) -> None:
        """A caller with no live Slack client (e.g. a test) must not crash."""
        service = DiscoveryPersistenceService(db, installation.id)

        conv = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=None)

        assert conv.name == "unknown"

    @pytest.mark.asyncio
    async def test_users_info_failure_falls_back_gracefully(self, db, installation) -> None:
        service = DiscoveryPersistenceService(db, installation.id)
        discovery = AsyncMock()
        discovery.enrich_user_info = AsyncMock(side_effect=RuntimeError("rate limited"))

        conv = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=discovery)

        assert conv.name == "unknown"

    @pytest.mark.asyncio
    async def test_channels_are_unaffected(self, db, installation) -> None:
        """The fix must only touch the im path — public/private channels
        already carry a real name from Slack and must not be re-resolved."""
        service = DiscoveryPersistenceService(db, installation.id)
        discovery = _discovery_returning({"real_name": "should not be used"})

        conv = await service.get_or_create_conversation(
            {"id": "C1", "name": "accounts"}, "T1", discovery=discovery
        )

        assert conv.name == "accounts"
        discovery.enrich_user_info.assert_not_called()


class TestBackfillOfExistingUnknownDms:
    @pytest.mark.asyncio
    async def test_a_previously_broken_dm_is_repaired_on_the_next_sync(self, db, installation) -> None:
        """Fixes the exact two conversations already sitting in the live
        database as name="unknown" from before this fix existed."""
        service = DiscoveryPersistenceService(db, installation.id)

        # Simulate the old broken behaviour having already created the row.
        broken = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=None)
        assert broken.name == "unknown"

        discovery = _discovery_returning({"real_name": "Bilal Ahmed"})
        repaired = await service.get_or_create_conversation(DM_CONV_DATA, "T1", discovery=discovery)

        assert repaired.id == broken.id, "must repair the same row, not create a second one"
        assert repaired.name == "Bilal Ahmed"

    @pytest.mark.asyncio
    async def test_a_channel_named_unknown_is_not_touched(self, db, installation) -> None:
        """"unknown" repair is im-specific — a channel that happens to be named
        literally "unknown" must never be treated as a broken DM."""
        service = DiscoveryPersistenceService(db, installation.id)
        first = await service.get_or_create_conversation({"id": "C9", "name": "unknown"}, "T1")

        discovery = _discovery_returning({"real_name": "should not apply"})
        second = await service.get_or_create_conversation(
            {"id": "C9", "name": "unknown"}, "T1", discovery=discovery
        )

        assert second.id == first.id
        assert second.name == "unknown"
