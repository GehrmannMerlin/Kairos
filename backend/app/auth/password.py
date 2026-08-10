"""Password hashing via pwdlib (Argon2id recommended profile).

Passwords never appear in plaintext, logs, or any response payload; they only
flow into ``hash_password`` / ``verify_password``.
"""

from __future__ import annotations

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _password_hash.verify(plain, hashed)
