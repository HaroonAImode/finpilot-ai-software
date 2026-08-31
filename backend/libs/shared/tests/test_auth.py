"""JWT creation and verification.

Security-critical: every service's tenancy boundary depends on these claims
being trustworthy, so the negative cases matter more than the happy path.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from shared.auth import (
    ALGORITHM, TokenError, bearer_token_from_header, create_access_token, decode_access_token,
)

SECRET = "test-secret-key-at-least-32-characters-long"
OTHER_SECRET = "a-completely-different-secret-key-value-here"


def _make(**overrides):
    kwargs = dict(
        secret_key=SECRET,
        user_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        role="admin",
        email="ayesha@company.pk",
    )
    kwargs.update(overrides)
    return kwargs


def test_round_trip_preserves_identity() -> None:
    args = _make()
    token, expires_in = create_access_token(**args)

    claims = decode_access_token(token, secret_key=SECRET)

    assert claims.user_id == args["user_id"]
    assert claims.company_id == args["company_id"]
    assert claims.role == "admin"
    assert claims.email == "ayesha@company.pk"
    assert 890 < expires_in <= 900, "default access token should last ~15 minutes"


def test_token_signed_with_another_key_is_rejected() -> None:
    token, _ = create_access_token(**_make())
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key=OTHER_SECRET)


def test_expired_token_is_rejected() -> None:
    token, _ = create_access_token(**_make(expires_minutes=-1))
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key=SECRET)


def test_alg_none_token_is_rejected() -> None:
    """The classic JWT attack: re-sign with alg=none and hope the server trusts the header."""
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "company_id": str(uuid.uuid4()),
            "role": "admin",
            "typ": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_access_token(forged, secret_key=SECRET)


def test_refresh_token_cannot_be_used_as_an_access_token() -> None:
    """Different lifetime and revocation rules — the typ claim keeps them apart."""
    refresh_shaped = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "company_id": str(uuid.uuid4()),
            "typ": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=7),
        },
        SECRET,
        algorithm=ALGORITHM,
    )
    with pytest.raises(TokenError, match="not an access token"):
        decode_access_token(refresh_shaped, secret_key=SECRET)


def test_token_without_company_id_is_rejected() -> None:
    """company_id is the tenancy boundary; a token lacking it must never pass."""
    incomplete = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET,
        algorithm=ALGORITHM,
    )
    with pytest.raises(TokenError):
        decode_access_token(incomplete, secret_key=SECRET)


def test_garbage_and_empty_tokens_are_rejected() -> None:
    for bad in ["", "not.a.token", "aaa.bbb.ccc"]:
        with pytest.raises(TokenError):
            decode_access_token(bad, secret_key=SECRET)


def test_empty_secret_is_refused_on_both_sides() -> None:
    """An empty key must never silently produce or accept unverifiable tokens."""
    with pytest.raises(ValueError):
        create_access_token(**_make(secret_key=""))

    token, _ = create_access_token(**_make())
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key="")


def test_tampered_claims_are_rejected() -> None:
    """Swapping company_id in the payload must break the signature."""
    token, _ = create_access_token(**_make())
    header, payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        b'{"sub":"' + str(uuid.uuid4()).encode() + b'","company_id":"' + str(uuid.uuid4()).encode()
        + b'","typ":"access","exp":99999999999}'
    ).decode()
    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{forged_payload}.{signature}", secret_key=SECRET)


class TestBearerHeader:
    def test_extracts_token(self) -> None:
        assert bearer_token_from_header("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_scheme_is_case_insensitive(self) -> None:
        assert bearer_token_from_header("bearer abc.def.ghi") == "abc.def.ghi"

    @pytest.mark.parametrize("header", [None, "", "abc.def.ghi", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   "])
    def test_rejects_anything_else(self, header) -> None:
        with pytest.raises(TokenError):
            bearer_token_from_header(header)
