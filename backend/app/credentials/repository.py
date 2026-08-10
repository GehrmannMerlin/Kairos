"""CredentialRepository: owner-scoped access to credential rows."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.auth.errors import NotFoundError
from app.credentials.models import Credential, CredentialVersion


class CredentialRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def create(self, user_id: int, kind: str, name: str) -> Credential:
        cred = Credential(user_id=user_id, kind=kind, name=name, status="active")
        self._db.add(cred)
        self._db.commit()
        self._db.refresh(cred)
        return cred

    def get_owned(self, user_id: int, credential_id: int) -> Credential:
        cred = self._db.get(Credential, credential_id)
        if cred is None or cred.user_id != user_id:
            raise NotFoundError("资源不存在")
        return cred

    def get_version_owner(self, credential_version_id: int) -> int | None:
        """Owner of the credential that owns this version (None if unknown)."""
        row = self._db.get(CredentialVersion, credential_version_id)
        if row is None:
            return None
        cred = self._db.get(Credential, row.credential_id)
        return cred.user_id if cred is not None else None

    def credential_id_for_version(self, credential_version_id: int) -> int | None:
        row = self._db.get(CredentialVersion, credential_version_id)
        return row.credential_id if row is not None else None

    def next_version(self, credential_id: int) -> int:
        latest = self._db.scalar(
            select(CredentialVersion.version)
            .where(CredentialVersion.credential_id == credential_id)
            .order_by(CredentialVersion.version.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    def add_version(
        self,
        *,
        credential_id: int,
        version: int,
        algorithm: str,
        key_version: str,
        nonce: bytes,
        wrapped_dek_nonce: bytes,
        secret_ciphertext: bytes,
        wrapped_dek: bytes,
    ) -> CredentialVersion:
        row = CredentialVersion(
            credential_id=credential_id,
            version=version,
            algorithm=algorithm,
            key_version=key_version,
            nonce=nonce,
            wrapped_dek_nonce=wrapped_dek_nonce,
            secret_ciphertext=secret_ciphertext,
            wrapped_dek=wrapped_dek,
            status="active",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_active_version(self, credential_id: int) -> CredentialVersion | None:
        return self._db.scalar(
            select(CredentialVersion)
            .where(
                CredentialVersion.credential_id == credential_id,
                CredentialVersion.status == "active",
            )
            .order_by(CredentialVersion.version.desc())
            .limit(1)
        )

    def get_version(self, credential_version_id: int) -> CredentialVersion | None:
        return self._db.get(CredentialVersion, credential_version_id)

    def retire_and_zero(self, credential_id: int, version_id: int) -> None:
        row = self._db.get(CredentialVersion, version_id)
        if row is not None:
            row.status = "retired"
            row.secret_ciphertext = b""
            row.wrapped_dek = b""
            self._db.commit()

    def disable(self, credential_id: int) -> None:
        self._db.execute(
            update(Credential).where(Credential.id == credential_id).values(status="disabled")
        )
        self._db.commit()
