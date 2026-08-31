"""DELETE /files/{id} — soft delete. A File mirrors a real Slack message, so
deleting it here must only hide it from FinPilot: never touch Slack, never
touch the S3 copy, and never come back on the next sync (discovery.py's
upsert never touches deleted_at — see that module's own comment).
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
def seeded(db_session_factory):
    import asyncio

    async def _seed():
        async with db_session_factory() as db:
            workspace = Workspace(slack_team_id="T1", name="Test", domain="t", is_enterprise=False)
            other_workspace = Workspace(slack_team_id="T2", name="Other", domain="o", is_enterprise=False)
            db.add_all([workspace, other_workspace])
            await db.flush()
            installation = Installation(
                company_id=uuid.uuid4(), workspace_id=workspace.id,
                bot_token_encrypted="e", bot_user_id="U1", scopes=["files:read"],
            )
            db.add(installation)
            await db.flush()

            other_installation = Installation(
                company_id=uuid.uuid4(), workspace_id=other_workspace.id,
                bot_token_encrypted="e2", bot_user_id="U2", scopes=["files:read"],
            )
            db.add(other_installation)
            await db.flush()

            conversation = Conversation(
                installation_id=installation.id, slack_conversation_id="C1",
                conversation_type=ConversationType.public, name="accounts",
            )
            other_conversation = Conversation(
                installation_id=other_installation.id, slack_conversation_id="C2",
                conversation_type=ConversationType.public, name="accounts",
            )
            db.add_all([conversation, other_conversation])
            await db.flush()

            file_obj = _file(installation.id, conversation.id, "F1")
            theirs = _file(other_installation.id, other_conversation.id, "F2")
            db.add_all([file_obj, theirs])
            await db.commit()

            return installation, other_installation, file_obj, theirs

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestDelete:
    def test_a_deleted_file_disappears_from_the_list(self, client, seeded) -> None:
        installation, _, file_obj, _ = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.delete(f"/api/v1/slack/files/{file_obj.id}")
        assert response.status_code == 204

        assert client.get("/api/v1/slack/files/").json()["total"] == 0

    def test_a_deleted_file_404s_by_id_too(self, client, seeded) -> None:
        """Not just hidden from the list — every direct link (preview,
        content, retry) has to agree it's gone."""
        installation, _, file_obj, _ = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        client.delete(f"/api/v1/slack/files/{file_obj.id}")
        assert client.get(f"/api/v1/slack/files/{file_obj.id}").status_code == 404

    def test_deleting_another_installation_s_file_is_a_404(self, client, seeded) -> None:
        """The security property: deleting must never reach a file outside
        this installation, even by guessing a valid id from another tenant."""
        installation, other_installation, _, theirs = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.delete(f"/api/v1/slack/files/{theirs.id}")
        assert response.status_code == 404

        # And it survives, untouched, from the owning installation's own view.
        app.dependency_overrides[get_installation_for_company] = lambda: other_installation
        assert client.get("/api/v1/slack/files/").json()["total"] == 1

    def test_deleting_an_unknown_file_is_a_404(self, client, seeded) -> None:
        installation, _, _, _ = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation
        assert client.delete(f"/api/v1/slack/files/{uuid.uuid4()}").status_code == 404

    def test_deleting_twice_is_still_a_404_the_second_time(self, client, seeded) -> None:
        """Not idempotent-200: the second call finds nothing to delete,
        which is exactly what a 404 means here."""
        installation, _, file_obj, _ = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        assert client.delete(f"/api/v1/slack/files/{file_obj.id}").status_code == 204
        assert client.delete(f"/api/v1/slack/files/{file_obj.id}").status_code == 404
