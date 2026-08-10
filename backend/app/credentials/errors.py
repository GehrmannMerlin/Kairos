"""Credential domain errors (M-03)."""

from __future__ import annotations

from app.credentials.crypto import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialError,
)

__all__ = [
    "CredentialError",
    "CredentialConfigurationError",
    "CredentialDecryptionError",
]
