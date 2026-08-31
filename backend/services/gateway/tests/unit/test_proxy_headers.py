"""Header handling at the Gateway boundary.

Downstream services trust `X-Company-ID` completely, so this file guards the
single assumption that makes that safe: the header the services see was written
by the Gateway from verified JWT claims, never by the caller.
"""
import uuid

import pytest

from shared.auth import TokenClaims

from app.core.proxy import IDENTITY_HEADERS, build_client_headers, build_upstream_headers


def _raw(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]


def _claims(company_id=None, user_id=None) -> TokenClaims:
    from datetime import datetime, timezone

    return TokenClaims(
        user_id=user_id or uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        role="admin",
        email="ayesha@khan.pk",
        expires_at=datetime.now(timezone.utc),
    )


class TestIdentityHeaderSpoofing:
    def test_client_supplied_company_id_is_replaced_by_the_verified_one(self) -> None:
        """The whole tenancy model: the caller's header must never survive."""
        real = uuid.uuid4()
        attacker_claim = uuid.uuid4()

        headers = build_upstream_headers(
            _raw({"X-Company-ID": str(attacker_claim), "Accept": "application/json"}),
            claims=_claims(company_id=real),
            trace_id="t-1",
        )

        assert headers["x-company-id"] == str(real)
        assert headers["x-company-id"] != str(attacker_claim)

    @pytest.mark.parametrize("header", IDENTITY_HEADERS)
    def test_every_identity_header_is_stripped_on_an_unauthenticated_route(self, header) -> None:
        """Auth routes carry no claims, so nothing may be injected — and nothing
        the caller sent may be forwarded either."""
        headers = build_upstream_headers(
            _raw({header: "smuggled-value"}), claims=None, trace_id="t-2"
        )
        assert header not in {k.lower() for k in headers}

    def test_spoofing_is_case_insensitive(self) -> None:
        """HTTP headers are case-insensitive; a naive check on the exact string
        would let 'x-company-id' through while blocking 'X-Company-ID'."""
        real = uuid.uuid4()
        for spelling in ["x-company-id", "X-COMPANY-ID", "X-Company-Id"]:
            headers = build_upstream_headers(
                _raw({spelling: str(uuid.uuid4())}), claims=_claims(company_id=real), trace_id="t"
            )
            assert headers["x-company-id"] == str(real)

    def test_all_verified_claims_are_forwarded(self) -> None:
        user_id, company_id = uuid.uuid4(), uuid.uuid4()
        headers = build_upstream_headers(
            _raw({}), claims=_claims(company_id=company_id, user_id=user_id), trace_id="t-3"
        )
        assert headers["x-user-id"] == str(user_id)
        assert headers["x-company-id"] == str(company_id)
        assert headers["x-user-role"] == "admin"
        assert headers["x-user-email"] == "ayesha@khan.pk"


class TestForwarding:
    def test_ordinary_headers_pass_through(self) -> None:
        headers = build_upstream_headers(
            _raw({"Accept": "application/json", "Authorization": "Bearer abc"}),
            claims=_claims(),
            trace_id="t-4",
        )
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"] == "Bearer abc"

    @pytest.mark.parametrize("header", ["Connection", "Transfer-Encoding", "Host", "Content-Length"])
    def test_hop_by_hop_headers_are_not_forwarded(self, header) -> None:
        """Forwarding these corrupts the upstream connection (RFC 9110 §7.6.1)."""
        headers = build_upstream_headers(_raw({header: "x"}), claims=None, trace_id="t-5")
        assert header.lower() not in {k.lower() for k in headers}

    def test_trace_id_is_always_added(self) -> None:
        headers = build_upstream_headers(_raw({}), claims=None, trace_id="trace-abc")
        assert headers["x-trace-id"] == "trace-abc"


class TestResponseHeaders:
    def test_set_cookie_survives(self) -> None:
        """The Auth Service's httpOnly refresh cookie must reach the browser or
        sessions will not survive a page reload."""
        headers = build_client_headers(
            {"set-cookie": "finpilot_refresh=abc; HttpOnly", "content-type": "application/json"},
            trace_id="t-6",
        )
        assert headers["set-cookie"] == "finpilot_refresh=abc; HttpOnly"

    def test_content_type_and_disposition_survive(self) -> None:
        """Inline PDF preview depends on both reaching the browser intact."""
        headers = build_client_headers(
            {"content-type": "application/pdf", "content-disposition": 'inline; filename="a.pdf"'},
            trace_id="t-7",
        )
        assert headers["content-type"] == "application/pdf"
        assert headers["content-disposition"] == 'inline; filename="a.pdf"'

    def test_hop_by_hop_response_headers_are_dropped(self) -> None:
        headers = build_client_headers(
            {"transfer-encoding": "chunked", "content-type": "application/json"}, trace_id="t-8"
        )
        assert "transfer-encoding" not in {k.lower() for k in headers}
