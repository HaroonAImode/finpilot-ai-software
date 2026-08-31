from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    """Fernet encryption for OAuth tokens stored outside the database key material."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt(self, encrypted_token: str) -> str:
        return self._fernet.decrypt(encrypted_token.encode()).decode()

    def encrypt_state(self, state: str) -> str:
        return self.encrypt(state)

    def decrypt_state(self, encrypted_state: str, ttl_seconds: int = 600) -> str:
        try:
            return self._fernet.decrypt(encrypted_state.encode(), ttl=ttl_seconds).decode()
        except InvalidToken as exc:
            raise ValueError("OAuth state is invalid or expired") from exc
