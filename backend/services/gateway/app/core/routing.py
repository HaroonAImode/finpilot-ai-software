"""Which upstream service serves which path prefix, and whether a JWT is required."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class Route:
    prefix: str
    upstream: str
    #: Auth routes must stay open — you cannot present a token before you have one.
    requires_auth: bool
    #: Exact paths inside a protected prefix that must stay open anyway.
    public_paths: frozenset[str] = frozenset()


# An OAuth provider redirects the user's *browser* here, so the request arrives
# as a plain navigation with no Authorization header — a bearer token cannot be
# attached to a third-party redirect. Requiring one would make connecting Slack
# impossible. The callback is not unprotected: it carries an encrypted,
# 10-minute-TTL state parameter holding the company id, checked against an
# httpOnly nonce cookie, which is what OAuth uses instead of a bearer token.
SLACK_PUBLIC_PATHS = frozenset({"/api/v1/slack/callback"})

# Same reasoning as Slack's: Google's OAuth redirect is a plain browser
# navigation with no Authorization header. Protected instead by the
# encrypted, 10-minute-TTL state parameter + httpOnly nonce cookie that
# email-connector's callback checks itself.
EMAIL_PUBLIC_PATHS = frozenset({"/api/v1/email/callback"})


def routing_table(settings: Settings) -> list[Route]:
    """Longest prefix wins, so order here is not load-bearing."""
    return [
        Route(prefix="/api/v1/auth", upstream=settings.auth_service_url, requires_auth=False),
        Route(
            prefix="/api/v1/slack",
            upstream=settings.slack_connector_url,
            requires_auth=True,
            public_paths=SLACK_PUBLIC_PATHS,
        ),
        Route(
            prefix="/api/v1/email",
            upstream=settings.email_connector_url,
            requires_auth=True,
            public_paths=EMAIL_PUBLIC_PATHS,
        ),
        # No public paths — Invoice Service has no OAuth-style callback, every
        # path here needs a verified caller (uploading/reading a company's
        # invoices is exactly the kind of data the whole auth layer exists to
        # protect).
        Route(prefix="/api/v1/invoices", upstream=settings.invoice_service_url, requires_auth=True),
        # Same reasoning as Invoice Service above: no OAuth callback, and a
        # company's uploaded documents are exactly what the auth layer exists
        # to protect, so every path requires a verified caller.
        Route(prefix="/api/v1/documents", upstream=settings.documents_service_url, requires_auth=True),
        # Same reasoning again: no OAuth callback, and a company's supplier
        # list and spend figures are exactly what the auth layer protects.
        Route(prefix="/api/v1/vendors", upstream=settings.vendors_service_url, requires_auth=True),
        # Architecture report §5.4. No OAuth callback, and a company's
        # expense ledger and Dashboard KPIs are exactly what the auth layer
        # protects — same reasoning as every route above.
        Route(prefix="/api/v1/expenses", upstream=settings.transactions_service_url, requires_auth=True),
        Route(prefix="/api/v1/transactions", upstream=settings.transactions_service_url, requires_auth=True),
        # Architecture report §5.5. No OAuth callback, and a company's
        # employee/payroll data is exactly what the auth layer protects —
        # same reasoning as every route above.
        Route(prefix="/api/v1/employees", upstream=settings.hr_service_url, requires_auth=True),
        # Architecture report §5.6. No OAuth callback, and a company's
        # purchase requests/orders/vendor quotes are exactly what the auth
        # layer protects — same reasoning as every route above.
        Route(prefix="/api/v1/procurement", upstream=settings.procurement_service_url, requires_auth=True),
        # Architecture report §5.10. No OAuth callback, and a company's
        # NTN/tax/address details are exactly what the auth layer protects
        # — same reasoning as every route above.
        Route(prefix="/api/v1/settings", upstream=settings.settings_service_url, requires_auth=True),
        # Architecture report §5.9. No OAuth callback, and a company's
        # financial reports are exactly what the auth layer protects —
        # same reasoning as every route above.
        Route(prefix="/api/v1/reports", upstream=settings.reports_service_url, requires_auth=True),
    ]


def match_route(path: str, settings: Settings) -> Route | None:
    matches = [route for route in routing_table(settings) if path.startswith(route.prefix)]
    if not matches:
        return None
    return max(matches, key=lambda route: len(route.prefix))


def requires_auth(route: Route, path: str) -> bool:
    """Whether this exact path needs a verified token.

    Matched exactly rather than by prefix: a `startswith` test would leave
    anything under `/api/v1/slack/callback...` open to the world.
    """
    if not route.requires_auth:
        return False
    return path.rstrip("/") not in {p.rstrip("/") for p in route.public_paths}
