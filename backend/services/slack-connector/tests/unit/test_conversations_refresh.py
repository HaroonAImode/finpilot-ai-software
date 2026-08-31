"""POST /conversations/refresh — live Slack discovery for the sync picker.

GET /conversations/ only ever shows what a previous sync already stored,
which is empty for a brand-new connection: the picker would show nothing to
choose from until after a full sync had already run once, unscoped. This
endpoint does a cheap, metadata-only conversations.list call (no message
walk, no downloads) and upserts before returning, so the picker always has a
complete, correctly-named list to select from immediately after connecting.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.main import app
from app.models import Conversation, ConversationType, Installation, Workspace

SLACK_CONVERSATIONS = [
    {"id": "C1", "name": "accounts", "is_channel": True},
    {"id": "D1", "is_im": True, "user": "U_OTHER"},
]


class _FakeDiscovery:
    """Stands in for DiscoveryService's async-context-manager + async-generator
    shape without making a real Slack call."""

    def __init__(self, conversations):
        self._conversations = conversations
        self.enrich_user_info = AsyncMock(return_value={"real_name": "Ayesha Khan"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_conversations(self):
        for conv in self._conversations:
            yield conv


@pytest.fixture
def db_session_factory():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def installation(db_session_factory):
    import asyncio

    async def _seed():
        async with db_session_factory() as db:
            workspace = Workspace(slack_team_id="T1", name="Test", domain="t", is_enterprise=False)
            db.add(workspace)
            await db.flush()
            inst = Installation(
                company_id=uuid.uuid4(), workspace_id=workspace.id,
                bot_token_encrypted="e", bot_user_id="U1", scopes=["files:read"],
            )
            db.add(inst)
            await db.commit()
            return inst

    return asyncio.get_event_loop().run_until_complete(_seed())


def _patch_discovery(monkeypatch, conversations=SLACK_CONVERSATIONS):
    import app.services.slack.discovery as discovery_module

    monkeypatch.setattr(
        discovery_module, "DiscoveryService", lambda *a, **k: _FakeDiscovery(conversations)
    )
    monkeypatch.setattr(
        "app.services.rate_limiter.get_rate_limiter", lambda: object()
    )
    monkeypatch.setattr(
        "app.core.security.TokenCipher.decrypt", lambda self, token: "xoxb-fake"
    )


class TestRefreshUpsertsFromSlack:
    def test_a_new_channel_is_created(self, client, installation, monkeypatch) -> None:
        _patch_discovery(monkeypatch)
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/conversations/refresh")

        assert response.status_code == 200
        names = {c["name"] for c in response.json()["conversations"]}
        assert "accounts" in names

    def test_a_dm_gets_its_real_name_resolved(self, client, installation, monkeypatch) -> None:
        """Uses the same DM-naming path as a full sync — the picker must not
        show a channel list full of "unknown" DMs."""
        _patch_discovery(monkeypatch)
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/conversations/refresh")

        names = {c["name"] for c in response.json()["conversations"]}
        assert "Ayesha Khan" in names
        assert "unknown" not in names

    def test_response_shape_matches_the_plain_get(self, client, installation, monkeypatch) -> None:
        """The picker calls refresh once then reads it like GET / — the two
        must never drift into different shapes."""
        _patch_discovery(monkeypatch)
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/conversations/refresh")

        body = response.json()
        assert set(body.keys()) == {"conversations", "total", "needs_attention"}
        assert body["total"] == len(body["conversations"])

    def test_calling_refresh_twice_does_not_duplicate_conversations(
        self, client, installation, monkeypatch
    ) -> None:
        _patch_discovery(monkeypatch)
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        client.post("/api/v1/slack/conversations/refresh")
        second = client.post("/api/v1/slack/conversations/refresh")

        assert second.json()["total"] == 2


class TestRefreshFailure:
    def test_slack_being_unreachable_returns_a_clean_502(self, client, installation, monkeypatch) -> None:
        import app.services.slack.discovery as discovery_module

        class _BoomDiscovery:
            async def __aenter__(self):
                raise ConnectionError("could not reach slack")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(discovery_module, "DiscoveryService", lambda *a, **k: _BoomDiscovery())
        monkeypatch.setattr("app.services.rate_limiter.get_rate_limiter", lambda: object())
        monkeypatch.setattr("app.core.security.TokenCipher.decrypt", lambda self, token: "xoxb-fake")
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/conversations/refresh")

        assert response.status_code == 502

    def test_a_stored_conversation_survives_a_failed_refresh(
        self, client, installation, monkeypatch, db_session_factory
    ) -> None:
        """A transient Slack outage during refresh must not lose or hide
        conversations a previous successful sync already found."""
        import asyncio

        async def _seed_existing():
            async with db_session_factory() as db:
                conv = Conversation(
                    installation_id=installation.id, slack_conversation_id="C_OLD",
                    conversation_type=ConversationType.public, name="already-synced",
                    last_synced=datetime.now(timezone.utc),
                )
                db.add(conv)
                await db.commit()

        asyncio.get_event_loop().run_until_complete(_seed_existing())

        import app.services.slack.discovery as discovery_module

        class _BoomDiscovery:
            async def __aenter__(self):
                raise ConnectionError("could not reach slack")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(discovery_module, "DiscoveryService", lambda *a, **k: _BoomDiscovery())
        monkeypatch.setattr("app.services.rate_limiter.get_rate_limiter", lambda: object())
        monkeypatch.setattr("app.core.security.TokenCipher.decrypt", lambda self, token: "xoxb-fake")
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        client.post("/api/v1/slack/conversations/refresh")  # fails, 502

        follow_up = client.get("/api/v1/slack/conversations/")
        assert follow_up.json()["total"] == 1
        assert follow_up.json()["conversations"][0]["name"] == "already-synced"
