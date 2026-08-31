"""Password hashing and refresh-token secrets.

Two different problems, deliberately handled two different ways:

* **Passwords** are hashed with Argon2id (architecture report §20). Argon2 is
  slow and memory-hard on purpose, which is what makes offline cracking of a
  stolen database expensive.

* **Refresh tokens** are high-entropy random strings we generated ourselves, so
  they need no slow hash — nobody can guess a 256-bit random value. They are
  stored as a plain SHA-256 digest so that a database leak does not hand out
  working sessions, while lookup stays a fast indexed equality check. Running
  Argon2 here would make every token refresh needlessly slow for no security
  gain.
"""
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Long enough that guessing is hopeless; urlsafe so it survives cookies/headers.
REFRESH_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against its hash, returning False rather than raising.

    Every failure mode collapses to False so callers cannot accidentally leak
    *why* a login failed (bad password vs corrupted hash) through differing
    error paths.
    """
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when a stored hash used weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """SHA-256 digest used as the database lookup key — see module docstring."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
