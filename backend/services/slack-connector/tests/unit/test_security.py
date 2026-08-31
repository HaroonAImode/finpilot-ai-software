from cryptography.fernet import Fernet

from app.core.security import TokenCipher


def test_token_cipher_keeps_plaintext_out_of_ciphertext() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt("xoxb-secret-token")

    assert encrypted != "xoxb-secret-token"
    assert cipher.decrypt(encrypted) == "xoxb-secret-token"
