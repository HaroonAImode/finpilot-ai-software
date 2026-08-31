"""POST /files/{id}/send-to-scanner's `target` param — Revenue Manager's
scan-to-revenue path (docs/superpowers/specs/2026-08-27-revenue-manager-
scan-design.md §7.2). The bridge function itself is covered in
test_scanner_bridge.py; this is the route's own validation and default.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.main import app
from app.models import Conversation, ConversationType, File, Installation, Workspace


def _file(installation_id, conversation_id, **overrides) -> File:
    defaults = dict(
        installation_id=installation_id, conversation_id=conversation_id,
        slack_file_id="F1", filename="receipt.jpg", file_type="jpg",
        mimetype="image/jpeg", size=100, created_at=datetime.now(timezone.utc),
        is_external=False, slack_permalink="https://example.com", message_ts="1.1",
        downloaded=True, s3_key="F1/receipt.jpg",
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
            db.add(workspace)
            await db.flush()
            installation = Installation(
                company_id=uuid.uuid4(), workspace_id=workspace.id,
                bot_token_encrypted="e", bot_user_id="U1", scopes=["files:read"],
            )
            db.add(installation)
            await db.flush()
            conversation = Conversation(
                installation_id=installation.id, slack_conversation_id="C1",
                conversation_type=ConversationType.public, name="accounts",
            )
            db.add(conversation)
            await db.flush()
            file_obj = _file(installation.id, conversation.id)
            db.add(file_obj)
            await db.commit()
            return installation, file_obj

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestTargetParam:
    def test_default_target_is_purchase(self, client, seeded) -> None:
        installation, file_obj = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        with patch(
            "app.api.routes.sync.send_file_to_scanner", AsyncMock(return_value={"job_id": "j1", "status": "done"}),
        ) as mock_send:
            response = client.post(f"/api/v1/slack/files/{file_obj.id}/send-to-scanner")

        assert response.status_code == 200
        assert mock_send.call_args.kwargs["invoice_type"] == "purchase"

    def test_target_sale_is_passed_through(self, client, seeded) -> None:
        installation, file_obj = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        with patch(
            "app.api.routes.sync.send_file_to_scanner", AsyncMock(return_value={"job_id": "j1", "status": "done"}),
        ) as mock_send:
            response = client.post(
                f"/api/v1/slack/files/{file_obj.id}/send-to-scanner", data={"target": "sale"},
            )

        assert response.status_code == 200
        assert mock_send.call_args.kwargs["invoice_type"] == "sale"

    def test_an_unknown_target_is_a_clean_400(self, client, seeded) -> None:
        installation, file_obj = seeded
        app.dependency_overrides[get_installation_for_company] = lambda: installation

        response = client.post(
            f"/api/v1/slack/files/{file_obj.id}/send-to-scanner", data={"target": "refund"},
        )

        assert response.status_code == 400
