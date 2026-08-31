# tests/unit/test_oauth.py
from app.core.config import Settings
from app.services.slack.oauth import BOT_SCOPES, authorization_url


def test_authorization_url_requests_required_bot_scopes() -> None:
    settings = Settings(
        SLACK_CLIENT_ID="client-id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )
    url = authorization_url(settings, "state")

    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    for scope in BOT_SCOPES:
        assert scope.replace(":", "%3A") in url or scope in url
