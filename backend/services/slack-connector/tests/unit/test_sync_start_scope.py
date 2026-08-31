"""POST /sync/ — the trigger side of the sync scope picker.

Covers the two things the picker actually sends: conversation_ids (which
conversations to touch this run) and window_days (using model_fields_set to
tell "explicitly chose All Time" apart from "didn't touch this control").
Both must reach the Celery task unresolved — resolution against the tenant
happens inside the worker, not here (see
test_filter_to_requested_conversations.py) — so these tests assert on what
run_sync.delay was called with, not on any synced result.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.main import app
from app.models import Installation, Workspace


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
                sync_window_days=180,
            )
            db.add(inst)
            await db.commit()
            return inst

    return asyncio.get_event_loop().run_until_complete(_seed())


@pytest.fixture
def captured_delay(monkeypatch):
    """run_sync.delay is what actually crosses into Celery — every assertion
    here is about what got handed to it, not about a sync running."""
    calls = []

    def _fake_delay(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("app.worker.run_sync.delay", _fake_delay)
    return calls


class TestConversationIdsPassThrough:
    def test_no_body_means_no_scope_restriction(self, client, installation, captured_delay) -> None:
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/", json={})

        assert response.status_code == 200
        _, kwargs = captured_delay[0]
        assert kwargs["conversation_ids"] is None

    def test_selected_conversation_ids_are_forwarded_as_strings(
        self, client, installation, captured_delay
    ) -> None:
        app.dependency_overrides[get_installation_for_company] = lambda: installation
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        response = client.post("/api/v1/slack/sync/", json={"conversation_ids": ids})

        assert response.status_code == 200
        _, kwargs = captured_delay[0]
        assert sorted(kwargs["conversation_ids"]) == sorted(ids)

    def test_empty_list_is_treated_as_no_restriction(self, client, installation, captured_delay) -> None:
        """An empty selection means "sync everything", matching the schema's
        documented contract, not "sync nothing"."""
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/", json={"conversation_ids": []})

        assert response.status_code == 200
        _, kwargs = captured_delay[0]
        assert kwargs["conversation_ids"] is None


class TestWindowDaysSemantics:
    def test_field_omitted_leaves_the_saved_setting_untouched(
        self, client, installation, captured_delay
    ) -> None:
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/", json={})

        assert response.status_code == 200
        assert installation.sync_window_days == 180

    def test_explicit_null_sets_all_time_and_is_distinct_from_omitted(
        self, client, installation, captured_delay
    ) -> None:
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/", json={"window_days": None})

        assert response.status_code == 200
        assert installation.sync_window_days is None

    def test_a_new_window_value_is_saved_as_the_default(
        self, client, installation, captured_delay
    ) -> None:
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/", json={"window_days": 90})

        assert response.status_code == 200
        assert installation.sync_window_days == 90

    def test_changing_the_window_clears_every_cursor(
        self, client, installation, captured_delay, db_session_factory
    ) -> None:
        import asyncio

        from app.models import Conversation, ConversationType

        async def _seed_conversation():
            async with db_session_factory() as db:
                conv = Conversation(
                    installation_id=installation.id, slack_conversation_id="C1",
                    conversation_type=ConversationType.public, name="accounts",
                    last_seen_ts="1700000000.000000",
                )
                db.add(conv)
                await db.commit()
                return conv.id

        conv_id = asyncio.get_event_loop().run_until_complete(_seed_conversation())
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        client.post("/api/v1/slack/sync/", json={"window_days": 30})

        async def _check():
            from sqlalchemy import select

            async with db_session_factory() as db:
                conv = await db.scalar(select(Conversation).where(Conversation.id == conv_id))
                return conv.last_seen_ts

        assert asyncio.get_event_loop().run_until_complete(_check()) is None


class TestFullResyncStillWorks:
    def test_full_resync_query_param_still_reaches_the_task(
        self, client, installation, captured_delay
    ) -> None:
        """Guards against the new body parameter accidentally shadowing the
        pre-existing ?full_resync=true query parameter."""
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post("/api/v1/slack/sync/?full_resync=true", json={})

        assert response.status_code == 200
        _, kwargs = captured_delay[0]
        assert kwargs["full_resync"] is True
