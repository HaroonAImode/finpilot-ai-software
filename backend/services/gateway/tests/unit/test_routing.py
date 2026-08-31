"""Path routing and which routes are open."""
import pytest

from app.core.config import Settings
from app.core.routing import match_route


def _settings() -> Settings:
    return Settings(
        JWT_SECRET_KEY="gateway-test-secret-at-least-32-characters",
        AUTH_SERVICE_URL="http://auth:8001",
        SLACK_CONNECTOR_URL="http://slack-connector:8010",
        EMAIL_CONNECTOR_URL="http://email-connector:8011",
        INVOICE_SERVICE_URL="http://invoice-service:8002",
    )


@pytest.mark.parametrize(
    "path,expected_upstream",
    [
        ("/api/v1/auth/login", "http://auth:8001"),
        ("/api/v1/auth/refresh", "http://auth:8001"),
        ("/api/v1/slack/status", "http://slack-connector:8010"),
        ("/api/v1/slack/files/abc/content", "http://slack-connector:8010"),
        ("/api/v1/email/status", "http://email-connector:8011"),
        ("/api/v1/email/files/abc/content", "http://email-connector:8011"),
        ("/api/v1/invoices/scan", "http://invoice-service:8002"),
        ("/api/v1/invoices/abc-123", "http://invoice-service:8002"),
    ],
)
def test_paths_route_to_the_right_service(path, expected_upstream) -> None:
    route = match_route(path, _settings())
    assert route is not None and route.upstream == expected_upstream


def test_auth_routes_are_open() -> None:
    """You cannot present a token before you have one."""
    assert match_route("/api/v1/auth/login", _settings()).requires_auth is False


def test_slack_routes_require_a_token() -> None:
    assert match_route("/api/v1/slack/files/", _settings()).requires_auth is True


def test_email_routes_require_a_token() -> None:
    assert match_route("/api/v1/email/status", _settings()).requires_auth is True


def test_invoice_routes_require_a_token() -> None:
    """No OAuth-style callback here, so unlike Slack/Email there is no
    public path to carve out — every invoice route needs a verified caller."""
    assert match_route("/api/v1/invoices/scan", _settings()).requires_auth is True


# Every backend-service prefix below was in this list at some point
# before it was built — see git history for the order. What's left
# unbuilt is the WhatsApp connector and AI Engine's chat/insights halves
# (AI Engine's OCR half isn't routed through the Gateway at all — see
# its own docstring in docker-compose.yml — so "/api/v1/ai" never routes
# either way).
@pytest.mark.parametrize(
    "path", ["/", "/api/v1/whatsapp", "/api/v1/ai", "/admin", "/api/v1"],
)
def test_unknown_paths_do_not_route(path) -> None:
    """A service with no route must 404 rather than fall through to something."""
    assert match_route(path, _settings()) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/documents", "/api/v1/vendors", "/api/v1/transactions", "/api/v1/expenses",
        "/api/v1/employees", "/api/v1/procurement", "/api/v1/settings", "/api/v1/reports",
    ],
)
def test_built_services_route_and_require_auth(path) -> None:
    """All were added after the original table; none has an OAuth-style
    callback, so every path needs a verified caller."""
    route = match_route(path, _settings())
    assert route is not None
    assert route.requires_auth is True


def test_longest_prefix_wins() -> None:
    """Guards the ordering rule the table relies on, so adding a more specific
    route later cannot be silently shadowed by a broader one."""
    from app.core.routing import Route, routing_table

    settings = _settings()
    table = routing_table(settings)
    assert all(isinstance(route, Route) for route in table)

    matched = match_route("/api/v1/slack/sync/", settings)
    assert matched.prefix == "/api/v1/slack"


class TestOAuthCallbackIsPublic:
    """An OAuth provider redirects the user's browser to /callback, so the
    request arrives with no Authorization header. Requiring a token there would
    make connecting Slack impossible."""

    def test_callback_does_not_require_a_token(self) -> None:
        from app.core.routing import requires_auth

        settings = _settings()
        route = match_route("/api/v1/slack/callback", settings)
        assert requires_auth(route, "/api/v1/slack/callback") is False

    def test_every_other_slack_route_still_requires_one(self) -> None:
        from app.core.routing import requires_auth

        settings = _settings()
        for path in [
            "/api/v1/slack/status",
            "/api/v1/slack/files/",
            "/api/v1/slack/sync/",
            "/api/v1/slack/connect",
            "/api/v1/slack/installation",
        ]:
            route = match_route(path, settings)
            assert requires_auth(route, path) is True, f"{path} must stay protected"

    def test_public_path_is_matched_exactly_not_by_prefix(self) -> None:
        """A startswith test would leave /callback/../files wide open."""
        from app.core.routing import requires_auth

        settings = _settings()
        for sneaky in [
            "/api/v1/slack/callbackx",
            "/api/v1/slack/callback/files",
            "/api/v1/slack/callback-extra",
        ]:
            route = match_route(sneaky, settings)
            assert requires_auth(route, sneaky) is True, f"{sneaky} must not be public"


class TestEmailOAuthCallbackIsPublic:
    """Same reasoning as Slack's — Google's OAuth redirect carries no token."""

    def test_callback_does_not_require_a_token(self) -> None:
        from app.core.routing import requires_auth

        settings = _settings()
        route = match_route("/api/v1/email/callback", settings)
        assert requires_auth(route, "/api/v1/email/callback") is False

    def test_every_other_email_route_still_requires_one(self) -> None:
        from app.core.routing import requires_auth

        settings = _settings()
        for path in [
            "/api/v1/email/status",
            "/api/v1/email/files/",
            "/api/v1/email/sync/",
            "/api/v1/email/connect",
            "/api/v1/email/account",
        ]:
            route = match_route(path, settings)
            assert requires_auth(route, path) is True, f"{path} must stay protected"
