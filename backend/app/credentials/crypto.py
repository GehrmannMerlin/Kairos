"""Envelope encryption primitives (AES-256-GCM).

Layout (envelope):
  secret ciphertext   <- AES-GCM(DEK, nonce, secret, aad)
  wrapped DEK         <- AES-GCM(KEK, wrapped_dek_nonce, DEK, aad)

Only the KEK (from env) is secret to the process; the database stores the
ciphertext + wrapped DEK + nonces + algorithm + key version. AAD binds
owner_id, credential_id and version to prevent cross-object ciphertext
substitution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

GCM_ALGORITHM = "aes-256-gcm"
NONCE_SIZE = 12
DEK_SIZE = 32
KEK_SIZE = 32


class CredentialError(Exception):
    code: str = "CREDENTIAL_ERROR"


class CredentialConfigurationError(CredentialError):
    code = "CREDENTIAL_CONFIGURATION_ERROR"


class CredentialDecryptionError(CredentialError):
    code = "CREDENTIAL_DECRYPTION_ERROR"


def master_key_from_env_value(value: str | None) -> bytes:
    """Parse a 64-hex-char (32-byte) KEK from the environment value."""
    if not value:
        raise CredentialConfigurationError(
            "KAIROS_CREDENTIAL_MASTER_KEY is not set; generate one with "
            "scripts/generate_master_key.py and add it to .env"
        )
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise CredentialConfigurationError(
            "KAIROS_CREDENTIAL_MASTER_KEY must be 64 hex chars"
        ) from exc
    if len(key) != KEK_SIZE:
        raise CredentialConfigurationError("KAIROS_CREDENTIAL_MASTER_KEY must be exactly 32 bytes")
    return key


def build_aad(user_id: int, credential_id: int, version: int) -> bytes:
    return f"{user_id}:{credential_id}:{version}".encode()


@dataclass(frozen=True)
class EncryptedSecret:
    algorithm: str
    key_version: str
    nonce: bytes
    wrapped_dek_nonce: bytes
    secret_ciphertext: bytes
    wrapped_dek: bytes


def encrypt_secret(
    *, kek: bytes, secret: str, aad: bytes, key_version: str = "k1"
) -> EncryptedSecret:
    dek = os.urandom(DEK_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    wrapped_dek_nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(dek).encrypt(nonce, secret.encode("utf-8"), aad)
    wrapped_dek = AESGCM(kek).encrypt(wrapped_dek_nonce, dek, aad)
    return EncryptedSecret(
        algorithm=GCM_ALGORITHM,
        key_version=key_version,
        nonce=nonce,
        wrapped_dek_nonce=wrapped_dek_nonce,
        secret_ciphertext=ciphertext,
        wrapped_dek=wrapped_dek,
    )


def decrypt_secret(*, kek: bytes, blob: EncryptedSecret, aad: bytes) -> str:
    try:
        dek = AESGCM(kek).decrypt(blob.wrapped_dek_nonce, blob.wrapped_dek, aad)
        plaintext = AESGCM(dek).decrypt(blob.nonce, blob.secret_ciphertext, aad)
    except InvalidTag as exc:
        raise CredentialDecryptionError(
            "credential could not be decrypted (tampered or wrong key)"
        ) from exc
    return plaintext.decode("utf-8")
