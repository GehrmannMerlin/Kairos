"""Session token handling.

The browser holds the raw token in an HttpOnly cookie; the database stores only
``sha256(token)`` so a DB leak never exposes usable tokens.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
