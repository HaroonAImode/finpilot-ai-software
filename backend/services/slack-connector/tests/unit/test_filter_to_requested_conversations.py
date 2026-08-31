"""SyncOrchestrator._filter_to_requested_conversations — the tenant boundary
for the sync scope picker.

conversation_ids arrive as plain UUID strings that crossed a process boundary
(API request -> Celery -> here), so nothing about them has been re-checked
against who is actually running this sync job. This is the one place that
check happens: resolve each id to a Conversation row scoped to this
installation_id, and only Slack ids that survive that scoping ever reach the
sync walk. A UUID from another company's conversation must be silently
dropped, never honoured — the picker crossing tenant lines here would mean
one company's sync request could pull another company's channel.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Conversation, ConversationType, Installation, Workspace
from app.models.base import Base
from app.services.sync_orchestrator import SyncOrchestrator


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def two_installations(db: AsyncSession):
    my_workspace = Workspace(slack_team_id="T1", name="Mine", domain="mine", is_enterprise=False)
    their_workspace = Workspace(slack_team_id="T2", name="Theirs", domain="theirs", is_enterprise=False)
    db.add_all([my_workspace, their_workspace])
    await db.flush()

    mine = Installation(
        company_id=uuid.uuid4(), workspace_id=my_workspace.id,
        bot_token_encrypted="e", bot_user_id="U1", scopes=["files:read"],
    )
    theirs = Installation(
        company_id=uuid.uuid4(), workspace_id=their_workspace.id,
        bot_token_encrypted="e", bot_user_id="U2", scopes=["files:read"],
    )
    db.add_all([mine, theirs])
    await db.flush()

    accounts = Conversation(
        installation_id=mine.id, slack_conversation_id="C_ACCOUNTS",
        conversation_type=ConversationType.public, name="accounts",
    )
    vendors = Conversation(
        installation_id=mine.id, slack_conversation_id="C_VENDORS",
        conversation_type=ConversationType.public, name="vendors",
    )
    other_company_channel = Conversation(
        installation_id=theirs.id, slack_conversation_id="C_SECRET",
        conversation_type=ConversationType.public, name="their-private-channel",
    )
    db.add_all([accounts, vendors, other_company_channel])
    await db.commit()

    return mine, accounts, vendors, other_company_channel


def _orchestrator(db, installation_id, conversation_ids) -> SyncOrchestrator:
    orch = SyncOrchestrator.__new__(SyncOrchestrator)  # skip __init__ and its live deps
    orch.db = db
    orch.installation_id = installation_id
    orch.conversation_ids = conversation_ids
    return orch


DISCOVERED = [
    {"id": "C_ACCOUNTS", "name": "accounts"},
    {"id": "C_VENDORS", "name": "vendors"},
    {"id": "C_SECRET", "name": "their-private-channel"},
    {"id": "C_UNSYNCED", "name": "never seen before"},
]


class TestFiltersToRequestedScope:
    @pytest.mark.asyncio
    async def test_keeps_only_the_requested_conversation(self, db, two_installations) -> None:
        mine, accounts, vendors, _ = two_installations
        orch = _orchestrator(db, mine.id, [str(accounts.id)])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert [c["id"] for c in result] == ["C_ACCOUNTS"]

    @pytest.mark.asyncio
    async def test_multiple_requested_conversations_all_pass_through(self, db, two_installations) -> None:
        mine, accounts, vendors, _ = two_installations
        orch = _orchestrator(db, mine.id, [str(accounts.id), str(vendors.id)])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert {c["id"] for c in result} == {"C_ACCOUNTS", "C_VENDORS"}


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_a_conversation_id_from_another_company_is_dropped(self, db, two_installations) -> None:
        """The core security property: naming another company's conversation
        UUID must not sync that company's channel, even though both ids are
        just UUIDs on the wire with no other distinguishing marker."""
        mine, accounts, vendors, other_company_channel = two_installations
        orch = _orchestrator(db, mine.id, [str(other_company_channel.id)])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert result == []

    @pytest.mark.asyncio
    async def test_mixing_own_and_foreign_ids_keeps_only_the_owned_one(self, db, two_installations) -> None:
        mine, accounts, vendors, other_company_channel = two_installations
        orch = _orchestrator(
            db, mine.id, [str(accounts.id), str(other_company_channel.id)]
        )

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert [c["id"] for c in result] == ["C_ACCOUNTS"]


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_a_requested_id_that_was_never_synced_yields_no_match(self, db, two_installations) -> None:
        """Requesting a conversation not yet stored (e.g. a stale picker
        selection) drops it rather than raising — one bad id should not fail
        an otherwise-valid partial sync."""
        mine, *_ = two_installations
        orch = _orchestrator(db, mine.id, [str(uuid.uuid4())])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert result == []

    @pytest.mark.asyncio
    async def test_a_malformed_uuid_string_is_dropped_not_raised(self, db, two_installations) -> None:
        mine, accounts, _, _ = two_installations
        orch = _orchestrator(db, mine.id, ["not-a-uuid", str(accounts.id)])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert [c["id"] for c in result] == ["C_ACCOUNTS"]

    @pytest.mark.asyncio
    async def test_all_malformed_ids_yields_empty_without_querying(self, db, two_installations) -> None:
        mine, *_ = two_installations
        orch = _orchestrator(db, mine.id, ["nope", "still-not-a-uuid"])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert result == []

    @pytest.mark.asyncio
    async def test_discovered_conversation_not_in_the_requested_set_is_excluded(
        self, db, two_installations
    ) -> None:
        """A conversation Slack still reports, but that the user did not tick
        in the picker, must not sneak into a scoped run."""
        mine, accounts, vendors, _ = two_installations
        orch = _orchestrator(db, mine.id, [str(accounts.id)])

        result = await orch._filter_to_requested_conversations(DISCOVERED)

        assert "C_VENDORS" not in {c["id"] for c in result}
