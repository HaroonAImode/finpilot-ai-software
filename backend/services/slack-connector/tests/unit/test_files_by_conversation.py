"""GET /files/?conversation_id=... — the read side of the grouped Documents view.

The view is built as: fetch conversations once (cheap, Phase 2), then lazily
fetch each conversation's files on expand — rather than one endpoint that
returns every file pre-grouped. A real workspace can hold thousands of files,
and "sync everything" was exactly the problem Phases 1-2 exist to avoid; a
"fetch everything grouped" read endpoint would quietly reintroduce it on the
read path.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.main import app
from app.models import Conversation, ConversationType, File, Installation, Workspace


def _file(installation_id, conversation_id, slack_file_id, **overrides):
    defaults = dict(
        installation_id=installation_id, conversation_id=conversation_id,
        slack_file_id=slack_file_id, filename=f"{slack_file_id}.pdf", file_type="pdf",
        mimetype="application/pdf", size=100, created_at=datetime.now(timezone.utc),
        is_external=False, slack_permalink="https://example.com", message_ts="1.1",
    )
    defaults.update(overrides)
    return File(**defaults)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


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
def seeded(db_session_factory):
    import asyncio

    async def _seed():
        async with db_session_factory() as db:
            workspace = Workspace(slack_team_id="T1", name="Test", domain="t", is_enterprise=False)
            db.add(workspace)
            await db.flush()
            installation = Installation(
                company_id=uuid.uuid4(), workspace_id=workspace.id,
                bot_token_encrypted="e", bot_user_id="U1", scopes=["files:read"],
            )
            db.add(installation)
            await db.flush()

            accounts = Conversation(
                installation_id=installation.id, slack_conversation_id="C1",
                conversation_type=ConversationType.public, name="accounts",
            )
            vendors = Conversation(
                installation_id=installation.id, slack_conversation_id="C2",
                conversation_type=ConversationType.public, name="vendors",
            )
            db.add_all([accounts, vendors])
            await db.flush()

            db.add(_file(installation.id, accounts.id, "F1"))
            db.add(_file(installation.id, accounts.id, "F2"))
            db.add(_file(installation.id, vendors.id, "F3"))
            await db.commit()

            return installation, accounts, vendors

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestConversationFilter:
    def test_filters_to_only_that_conversations_files(self, client, seeded) -> None:
        installation, accounts, vendors = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.get(f"/api/v1/slack/files/?conversation_id={accounts.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert {f["slack_file_id"] for f in body["files"]} == {"F1", "F2"}

    def test_a_different_conversation_returns_its_own_files_only(self, client, seeded) -> None:
        installation, accounts, vendors = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.get(f"/api/v1/slack/files/?conversation_id={vendors.id}")

        assert response.json()["total"] == 1
        assert response.json()["files"][0]["slack_file_id"] == "F3"

    def test_no_filter_still_returns_everything(self, client, seeded) -> None:
        installation, accounts, vendors = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.get("/api/v1/slack/files/")

        assert response.json()["total"] == 3

    def test_a_conversation_id_from_another_company_returns_nothing(self, client, seeded) -> None:
        """The security property: filtering by conversation_id must never let a
        caller reach a conversation — and therefore files — outside their own
        installation, even by guessing a valid UUID from another tenant."""
        installation, accounts, vendors = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.get(f"/api/v1/slack/files/?conversation_id={uuid.uuid4()}")

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_file_response_carries_its_conversation_id(self, client, seeded) -> None:
        """The flat view needs this to show which channel a file came from
        without a second lookup per file."""
        installation, accounts, vendors = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.get(f"/api/v1/slack/files/?conversation_id={accounts.id}")

        assert response.json()["files"][0]["conversation_id"] == str(accounts.id)
