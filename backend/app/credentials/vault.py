"""CredentialVault: the only place secrets are encrypted/decrypted (M-03).

``read_for_execution`` must only be called from controlled backend execution
paths (ProviderService connection tests; later Activities). There is no HTTP
endpoint that returns a plaintext secret.
"""

from __future__ import annotations

from app.auth.errors import NotFoundError
from app.credentials import crypto
from app.credentials.repository import CredentialRepository


class CredentialInfo:
    def __init__(self, credential_id: int, version: int, version_id: int) -> None:
        self.credential_id = credential_id
        self.version = version
        self.version_id = version_id


class CredentialVault:
    def __init__(
        self,
        *,
        master_key: bytes,
        key_version: str,
        repository: CredentialRepository,
    ) -> None:
        self._kek = master_key
        self._key_version = key_version
        self._repo = repository

    def store_secret(self, *, user_id: int, kind: str, name: str, secret: str) -> CredentialInfo:
        cred = self._repo.create(user_id, kind, name)
        return self._encrypt_new_version(user_id, cred.id, secret)

    def rotate(self, *, user_id: int, credential_id: int, secret: str) -> CredentialInfo:
        cred = self._repo.get_owned(user_id, credential_id)
        return self._encrypt_new_version(user_id, cred.id, secret)

    def rotate_for_config(
        self, *, user_id: int, credential_version_id: int, secret: str
    ) -> CredentialInfo:
        """Rotate the credential referenced by a frozen config version."""
        credential_id = self._repo.credential_id_for_version(credential_version_id)
        if credential_id is None:
            raise NotFoundError("资源不存在")
        return self.rotate(user_id=user_id, credential_id=credential_id, secret=secret)

    def read_for_execution(self, *, user_id: int, credential_version_id: int) -> str:
        row = self._get_owned_version(user_id, credential_version_id)
        if row.status != "active":
            raise crypto.CredentialError("credential version is retired")
        blob = crypto.EncryptedSecret(
            algorithm=row.algorithm,
            key_version=row.key_version,
            nonce=row.nonce,
            wrapped_dek_nonce=row.wrapped_dek_nonce,
            secret_ciphertext=row.secret_ciphertext,
            wrapped_dek=row.wrapped_dek,
        )
        aad = crypto.build_aad(user_id, row.credential_id, row.version)
        return crypto.decrypt_secret(kek=self._kek, blob=blob, aad=aad)

    def revoke(self, *, user_id: int, credential_id: int) -> None:
        cred = self._repo.get_owned(user_id, credential_id)
        active = self._repo.get_active_version(cred.id)
        if active is not None:
            self._repo.retire_and_zero(cred.id, active.id)
        self._repo.disable(cred.id)

    def revoke_by_version(self, *, user_id: int, credential_version_id: int) -> None:
        """Revoke the credential referenced by a frozen config version."""
        credential_id = self._repo.credential_id_for_version(credential_version_id)
        if credential_id is None:
            raise NotFoundError("资源不存在")
        self.revoke(user_id=user_id, credential_id=credential_id)

    def get_active(self, *, user_id: int, credential_id: int):
        self._repo.get_owned(user_id, credential_id)
        return self._repo.get_active_version(credential_id)

    def credential_configured(self, *, user_id: int, credential_id: int) -> bool:
        return self.get_active(user_id=user_id, credential_id=credential_id) is not None

    def _encrypt_new_version(self, user_id: int, credential_id: int, secret: str) -> CredentialInfo:
        version = self._repo.next_version(credential_id)
        aad = crypto.build_aad(user_id, credential_id, version)
        blob = crypto.encrypt_secret(
            kek=self._kek, secret=secret, aad=aad, key_version=self._key_version
        )
        row = self._repo.add_version(
            credential_id=credential_id,
            version=version,
            algorithm=blob.algorithm,
            key_version=blob.key_version,
            nonce=blob.nonce,
            wrapped_dek_nonce=blob.wrapped_dek_nonce,
            secret_ciphertext=blob.secret_ciphertext,
            wrapped_dek=blob.wrapped_dek,
        )
        return CredentialInfo(credential_id, version, row.id)

    def _get_owned_version(self, user_id: int, credential_version_id: int):
        owner_id = self._repo.get_version_owner(credential_version_id)
        if owner_id != user_id:
            raise NotFoundError("资源不存在")
        row = self._repo.get_version(credential_version_id)
        if row is None:
            raise NotFoundError("资源不存在")
        return row
