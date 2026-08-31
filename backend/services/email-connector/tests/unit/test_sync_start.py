"""POST /sync/ — trigger side. Covers the same model_fields_set trick Slack's
picker uses for window_days (telling "explicitly chose N days" apart from
"didn't touch this control"), and that widening the window clears the
history cursor so the next sync doesn't resume from a narrower one.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_account_for_company
from app.db.session import get_db
from app.main import app
from app.models import EmailAccount, EmailProviderName


def _account(**overrides) -> EmailAccount:
    defaults = dict(
        company_id=uuid.uuid4(), provider=EmailProviderName.gmail, email_address="a@b.com",
        access_token_encrypted="x", refresh_token_encrypted="y",
        token_expires_at=datetime.now(timezone.utc), scopes=[], sync_window_days=180,
        sync_cursor="12345",
    )
    defaults.update(overrides)
    return EmailAccount(**defaults)


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
def account(db_session_factory):
    import asyncio

    async def _seed():
        async with db_session_factory() as db:
            acc = _account()
            db.add(acc)
            await db.commit()
            await db.refresh(acc)
            return acc

    return asyncio.get_event_loop().run_until_complete(_seed())


@pytest.fixture
def captured_delay(monkeypatch):
    calls = []

    def _fake_delay(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("app.worker.run_sync.delay", _fake_delay)
    return calls


class TestWindowDaysSemantics:
    def test_omitted_field_leaves_the_saved_window_and_cursor_untouched(
        self, client, account, captured_delay, db_session_factory
    ) -> None:
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post("/api/v1/email/sync/", json={})

        assert response.status_code == 200
        assert account.sync_window_days == 180
        assert account.sync_cursor == "12345"

    def test_explicit_window_change_clears_the_cursor(self, client, account, captured_delay) -> None:
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post("/api/v1/email/sync/", json={"sync_window_days": 30})

        assert response.status_code == 200
        assert account.sync_window_days == 30
        assert account.sync_cursor is None

    def test_run_sync_is_queued_with_the_new_job_id(self, client, account, captured_delay) -> None:
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post("/api/v1/email/sync/", json={})

        assert response.status_code == 200
        job_id = response.json()["sync_job_id"]
        args, kwargs = captured_delay[0]
        assert args[0] == job_id or kwargs.get("sync_job_id") == job_id
