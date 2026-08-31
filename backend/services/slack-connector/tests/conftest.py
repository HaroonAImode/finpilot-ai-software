"""
Pytest configuration and fixtures for the test suite.

Provides database fixtures, settings fixtures, and mocked Slack API responses.
"""
import asyncio
import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.models.base import Base
from app.db.session import get_db


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings():
    """Provide test settings."""
    return Settings(
        SLACK_CLIENT_ID="test_client_id",
        SLACK_CLIENT_SECRET="test_client_secret",
        SLACK_SIGNING_SECRET="test_signing_secret",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="minioadmin",
        S3_SECRET_ACCESS_KEY="minioadmin",
        S3_BUCKET_NAME="test-bucket",
        APP_ENV="test",
        LOG_LEVEL="DEBUG",
        FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )


@pytest_asyncio.fixture
async def db():
    """Provide an in-memory test database."""
    # Use SQLite in-memory for fast tests
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        echo=False,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
def mock_slack_responses():
    """Provide mock Slack API responses for fixtures."""
    return {
        "conversations.list": {
            "ok": True,
            "channels": [
                {
                    "id": "C1234567890",
                    "name": "general",
                    "is_channel": True,
                    "created": 1691000000,
                    "creator": "U1234567890",
                    "is_archived": False,
                    "is_general": True,
                    "topic": {"value": "Company-wide announcements"},
                    "purpose": {"value": "General discussion"},
                    "is_member": True,
                },
                {
                    "id": "C0987654321",
                    "name": "invoices",
                    "is_channel": True,
                    "created": 1691100000,
                    "creator": "U1234567890",
                    "is_archived": False,
                    "is_general": False,
                    "topic": {"value": "Invoice documents"},
                    "purpose": {"value": "Share invoices and billing docs"},
                    "is_member": True,
                },
            ],
            "response_metadata": {
                "next_cursor": "",
            },
        },
        "conversations.history": {
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "user": "U1234567890",
                    "text": "Here's the latest invoice",
                    "ts": "1691000000.000100",
                    "files": [
                        {
                            "id": "F1234567890",
                            "name": "invoice_2024_q4.pdf",
                            "title": "Q4 2024 Invoice",
                            "mimetype": "application/pdf",
                            "filetype": "pdf",
                            "pretty_type": "PDF",
                            "user": "U1234567890",
                            "editable": False,
                            "size": 1024000,
                            "mode": "hosted",
                            "is_external": False,
                            "external_type": None,
                            "is_public": False,
                            "public_url_shared": False,
                            "display_as_bot": False,
                            "username": "test_user",
                            "url_private": "https://files.slack.com/files-pri/T123-F1234567890/invoice.pdf",
                            "url_private_download": "https://files.slack.com/files-pri/T123-F1234567890/download/invoice.pdf",
                            "permalink": "https://test-workspace.slack.com/files/U1234567890/F1234567890/invoice.pdf",
                            "permalink_public": "",
                            "channels": ["C1234567890"],
                            "groups": [],
                            "ims": [],
                            "comments_count": 0,
                            "created": 1691000000,
                            "timestamp": 1691000000,
                            "is_starred": False,
                            "has_rich_preview": True,
                        }
                    ],
                },
            ],
            "response_metadata": {
                "next_cursor": "",
            },
        },
        "files.list": {
            "ok": True,
            "files": [
                {
                    "id": "F1234567890",
                    "name": "invoice_2024_q4.pdf",
                    "title": "Q4 2024 Invoice",
                    "mimetype": "application/pdf",
                    "filetype": "pdf",
                    "pretty_type": "PDF",
                    "user": "U1234567890",
                    "size": 1024000,
                    "mode": "hosted",
                    "is_external": False,
                    "created": 1691000000,
                    "channels": ["C1234567890"],
                    "groups": [],
                    "ims": [],
                },
            ],
            "response_metadata": {
                "next_cursor": "",
            },
        },
    }
