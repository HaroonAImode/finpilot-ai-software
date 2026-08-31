import json
import uuid

from app.core.security import TokenCipher


ENCRYPTION_KEY = "dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM="


def test_oauth_state_round_trips_company_id_and_nonce() -> None:
    from app.api.routes.auth import _build_state, _parse_state

    cipher = TokenCipher(ENCRYPTION_KEY)
    company_id = uuid.uuid4()

    encrypted_state, nonce = _build_state(cipher, company_id)
    parsed_company_id, parsed_nonce = _parse_state(cipher, encrypted_state)

    assert parsed_company_id == company_id
    assert parsed_nonce == nonce


def test_parse_state_rejects_tampered_payload() -> None:
    from app.api.routes.auth import _parse_state
    import pytest

    cipher = TokenCipher(ENCRYPTION_KEY)
    with pytest.raises(ValueError):
        _parse_state(cipher, "not-a-real-encrypted-state")
