"""Envelope encryption primitives (AES-256-GCM)."""

from __future__ import annotations

import pytest
from app.credentials import crypto


def test_roundtrip_secret() -> None:
    kek = bytes.fromhex("ab" * 32)
    blob = crypto.encrypt_secret(kek=kek, secret="sk-live-123", aad=b"owner:1:1")
    assert blob.algorithm == "aes-256-gcm"
    assert b"sk-live-123" not in blob.secret_ciphertext
    assert crypto.decrypt_secret(kek=kek, blob=blob, aad=b"owner:1:1") == "sk-live-123"


def test_tampered_ciphertext_fails() -> None:
    kek = bytes.fromhex("ab" * 32)
    blob = crypto.encrypt_secret(kek=kek, secret="secret", aad=b"owner:1:1")
    tampered = crypto.EncryptedSecret(
        algorithm=blob.algorithm,
        key_version=blob.key_version,
        nonce=blob.nonce,
        wrapped_dek_nonce=blob.wrapped_dek_nonce,
        secret_ciphertext=b"\x00" + blob.secret_ciphertext[1:],
        wrapped_dek=blob.wrapped_dek,
    )
    with pytest.raises(crypto.CredentialDecryptionError):
        crypto.decrypt_secret(kek=kek, blob=tampered, aad=b"owner:1:1")


def test_wrong_aad_fails() -> None:
    kek = bytes.fromhex("ab" * 32)
    blob = crypto.encrypt_secret(kek=kek, secret="secret", aad=b"owner:1:1")
    with pytest.raises(crypto.CredentialDecryptionError):
        crypto.decrypt_secret(kek=kek, blob=blob, aad=b"owner:9:1")


def test_master_key_derivation() -> None:
    kek = crypto.master_key_from_env_value("ab" * 32)
    assert kek == bytes.fromhex("ab" * 32)
    with pytest.raises(crypto.CredentialConfigurationError):
        crypto.master_key_from_env_value("too-short")
    with pytest.raises(crypto.CredentialConfigurationError):
        crypto.master_key_from_env_value(None)
