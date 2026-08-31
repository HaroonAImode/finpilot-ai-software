"""Pytest configuration and fixtures for the email-connector test suite."""
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.session import get_db
from app.models.base import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings():
    return Settings(
        GOOGLE_CLIENT_ID="test_client_id",
        GOOGLE_CLIENT_SECRET="test_client_secret",
        GOOGLE_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/8",
        S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="minioadmin",
        S3_SECRET_ACCESS_KEY="minioadmin",
        S3_BUCKET_NAME="test-bucket",
        APP_ENV="test",
        LOG_LEVEL="DEBUG",
        FRONTEND_BASE_URL="http://localhost:8080",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
