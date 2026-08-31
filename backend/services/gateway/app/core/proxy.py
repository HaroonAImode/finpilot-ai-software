"""Header handling for proxied requests.

The security of the whole downstream fleet rests on one rule here: identity
headers are **stripped from the incoming request and re-created from verified
claims**. Downstream services trust `X-Company-ID`; if the Gateway forwarded a
client-supplied one, any caller could read another company's data by adding a
header. Stripping happens whether or not the route requires auth, so an
unauthenticated path can never smuggle one through either.
"""
from __future__ import annotations

# Set only by the Gateway, from verified JWT claims. Any inbound copy is a
# spoofing attempt (or a confused client) and is discarded.
IDENTITY_HEADERS = ("x-user-id", "x-company-id", "x-user-role", "x-user-email")

# Hop-by-hop headers belong to a single connection and must not be forwarded
# (RFC 9110 §7.6.1). Host is dropped so httpx sets it for the upstream, and
# content-length because the body may be re-encoded.
HOP_BY_HOP_HEADERS = (
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
)

_STRIP_FROM_REQUEST = frozenset(IDENTITY_HEADERS + HOP_BY_HOP_HEADERS)
_STRIP_FROM_RESPONSE = frozenset(HOP_BY_HOP_HEADERS)


def build_upstream_headers(
    incoming: list[tuple[bytes, bytes]],
    *,
    claims=None,
    trace_id: str,
) -> dict[str, str]:
    """Forward the client's headers minus anything it must not control."""
    headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in incoming
        if key.decode("latin-1").lower() not in _STRIP_FROM_REQUEST
    }

    headers["x-trace-id"] = trace_id

    if claims is not None:
        headers["x-user-id"] = str(claims.user_id)
        headers["x-company-id"] = str(claims.company_id)
        headers["x-user-role"] = claims.role
        if claims.email:
            headers["x-user-email"] = claims.email

    return headers


def build_client_headers(upstream_headers, *, trace_id: str) -> dict[str, str]:
    """Pass the upstream response back, minus connection-scoped headers.

    set-cookie is deliberately preserved — the Auth Service's httpOnly refresh
    cookie has to reach the browser for sessions to survive a reload.
    """
    headers = {
        key: value
        for key, value in upstream_headers.items()
        if key.lower() not in _STRIP_FROM_RESPONSE
    }
    headers["x-trace-id"] = trace_id
    return headers
