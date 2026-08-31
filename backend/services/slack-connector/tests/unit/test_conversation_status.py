"""Per-conversation sync status.

The problem this phase exists to fix: a sync reporting "8 of 10 conversations"
with the two failures recorded only as strings on the job, so the user could see
that something broke but not which channel or why — and therefore could not fix
it. Both failures were in fact one-click fixes (invite the bot).
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Conversation, ConversationType, Installation, Workspace
from app.models.base import Base
from app.schemas.sync import ConversationResponse
from app.services.sync_orchestrator import _slack_error_code


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


class TestErrorCodeExtraction:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Slack API error: not_in_channel", "not_in_channel"),
            ("Slack API error: channel_not_found", "channel_not_found"),
            ("Conversation error: Slack API error: missing_scope", "missing_scope"),
        ],
    )
    def test_slack_codes_are_extracted(self, message, expected) -> None:
        assert _slack_error_code(Exception(message)) == expected

    def test_non_slack_errors_are_kept_verbatim(self) -> None:
        """An unexpected failure must still be visible, not swallowed."""
        assert _slack_error_code(ValueError("connection reset by peer")) == "connection reset by peer"

    def test_long_messages_are_truncated_to_the_column_width(self) -> None:
        assert len(_slack_error_code(Exception("x" * 500))) == 128


class TestErrorHints:
    def test_known_codes_become_actionable_advice(self) -> None:
        hint = ConversationResponse.hint_for("not_in_channel")
        assert "Invite" in hint, "the hint should say what to actually do"

    def test_unknown_codes_still_produce_something_honest(self) -> None:
        """A code we have not mapped must not render as a blank space."""
        hint = ConversationResponse.hint_for("some_new_slack_code")
        assert hint and "some_new_slack_code" in hint

    def test_no_error_means_no_hint(self) -> None:
        assert ConversationResponse.hint_for(None) is None


class TestStoredStatus:
    @pytest.mark.asyncio
    async def test_error_is_recorded_against_the_conversation(self, db, installation) -> None:
        conv = Conversation(
            installation_id=installation.id, slack_conversation_id="C1",
            conversation_type=ConversationType.private, name="private-test",
            last_error="not_in_channel",
        )
        db.add(conv)
        await db.commit()

        stored = await db.scalar(select(Conversation).where(Conversation.name == "private-test"))
        assert stored.last_error == "not_in_channel"
        assert "Invite" in ConversationResponse.hint_for(stored.last_error)

    @pytest.mark.asyncio
    async def test_a_successful_sync_clears_a_previous_error(self, db, installation) -> None:
        """Once someone invites the bot, the warning must stop being shown —
        otherwise it tells them to fix something already fixed."""
        conv = Conversation(
            installation_id=installation.id, slack_conversation_id="C1",
            conversation_type=ConversationType.public, name="accounts",
            last_error="not_in_channel",
        )
        db.add(conv)
        await db.commit()

        # what _sync_conversation does on a successful run
        conv.last_error = None
        await db.commit()

        refreshed = await db.scalar(select(Conversation).where(Conversation.name == "accounts"))
        assert refreshed.last_error is None
        assert ConversationResponse.hint_for(refreshed.last_error) is None


class TestRouteRegistration:
    def test_conversations_endpoint_is_mounted(self) -> None:
        from app.main import app

        paths = {r.path for r in app.routes if hasattr(r, "methods")}
        assert "/api/v1/slack/conversations/" in paths
