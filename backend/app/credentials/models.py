"""Credential / credential version / model config / search config persistence (M-03).

Versioning contract:
- ``credentials`` = logical identity (immutable). ``credential_versions`` = each
  encrypted secret (active vs retired). rotate -> new version; revoke retires the
  active version and zeroes its ciphertext.
- ``model_configs`` / ``search_configs`` are history rows: a logical config is
  identified by ``config_id`` and each edit appends a new ``version`` row marked
  ``is_current``. M-06 freezes (config_id, version).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


def _uuid() -> str:
    return uuid4().hex


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # model_api_key | search_api_key | cookie | username_password
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 网站凭据（M-10 / D-059）：domain 范围；CURRENT_TASK 绑定 task_id；
    # SAVED_DOMAIN 供同用户后续任务复用
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # CURRENT_TASK | SAVED_DOMAIN
    task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CredentialVersion(Base):
    __tablename__ = "credential_versions"
    __table_args__ = (UniqueConstraint("credential_id", "version", name="uq_cv_cred_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    key_version: Mapped[str] = mapped_column(String(20), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    wrapped_dek_nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    # active | retired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (UniqueConstraint("config_id", "version", name="uq_mc_config_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    config_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    credential_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_versions.id", ondelete="RESTRICT"), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connection_status: Mapped[str] = mapped_column(String(30), nullable=False, default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchConfig(Base):
    __tablename__ = "search_configs"
    __table_args__ = (UniqueConstraint("config_id", "version", name="uq_sc_config_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    config_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    credential_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_versions.id", ondelete="RESTRICT"), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    connection_status: Mapped[str] = mapped_column(String(30), nullable=False, default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
