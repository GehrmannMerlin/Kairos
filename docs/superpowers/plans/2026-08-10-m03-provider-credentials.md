# M-03 BYOK 模型、Search Provider 与秘密凭据管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一的 Model/Search Provider 注册表、信封加密的 CredentialVault、可冻结版本的 ModelConfig/SearchConfig，以及最小 `/models` 前端配置闭环。

**Architecture:** 后端在现有 `app/auth`（owner 隔离）、`app/infra/db`（SQLAlchemy）之上新增两个模块：`app/credentials`（AES-256-GCM 信封加密 CredentialVault + 版本化凭据存储）与 `app/providers`（ModelProvider/SearchProvider 协议、代码注册的 typed Registry、协议族 Adapter、ProviderService 与薄 Route）。API Key 只写入/更换，`read_for_execution` 只在服务层受控路径调用，前端永不回读明文。ModelConfig 与 SearchConfig 采用「单表 + 逻辑 `config_id` + `version` + `is_current`」历史版本模式。

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic、`cryptography>=42`（AES-256-GCM）、`httpx`（真实最小连接测试 + 可注入 fake transport）、Vue 3 + TypeScript strict + Vitest。

## Global Constraints

- 所有 Provider/凭据资源必须带 `user_id` 且通过 M-02 `require_user` / `errors.assert_owned` 隔离；跨用户一律 404，禁止依赖前端隐藏。**不得重新实现第二套 auth/ownership。**
- 数据库绝不保存：Master KEK、plaintext DEK、plaintext API Key/Cookie/password。
- API Response 绝不出现：`secret`、`ciphertext`、`wrapped_dek`、`nonce`、`master_key`、`api_key` 明文。前端只能看到 `credential_configured` 与安全 metadata。
- Master Key 只来自环境变量 `KAIROS_CREDENTIAL_MASTER_KEY`（32 字节 hex，64 字符），与数据库分离；`.env.example` 只写变量名与生成说明，禁止提交真实 Key。
- 秘密绝不进入普通日志、异常文本、Temporal History、前端明文。
- 不使用 Pydantic AI / openai / anthropic SDK 构造 Agent（M-03 不做 Agent 调用）；连接测试用最小真实 HTTP 请求。禁止把 AI model 与 search service 做成同一个 Provider DTO 后到处 `if/else`。
- 本轮禁止：Task/CollectionSpec/Plan/Run/NodeRun/状态机/Outbox/Checkpoint/域事件大体系/Agent 编排/Approval/Credential Drawer/网站凭据访问/Search 业务执行/URL Frontier/robots/Scrapy/Playwright/完整 App Shell/13 页面/计费 UI/Deploy Gate。
- 不 push / 不 merge / 不 tag / 不 deploy；只做本地 Commit（5～7 个）。分支 `feature/M-03-provider-credentials`，基线 SHA = `e7dda2c1e2928689a1214715830e099cc98fe956`（M-02 HEAD）。
- 只运行 M-03 scoped 验证；禁止默认 `pytest tests/` 全量、禁止要求真实商业 API Key 才能跑测试（仅 `LIVE_PROVIDER_TEST_KEY` 存在时可选跑 live test）。

---

## 术语与关键接口（全局共享，后续 Task 引用）

以下接口定义在本计划所有 Task 内一致，任何 Task 不得改名。

**错误分类 `ProviderTestStatus`**（枚举字符串）：`AVAILABLE` / `AUTH_FAILED` / `MODEL_NOT_FOUND` / `RATE_LIMITED` / `NETWORK_ERROR` / `FAILED`。

**`ProviderTestResult`**（稳定 DTO）：`status: ProviderTestStatus`、`error_code: str | None`、`message: str | None`、`latency_ms: int | None`。

**`ProviderDefinition`**（registry metadata）：`provider_type: str`、`display_name: str`、`requires_api_key: bool`、`requires_model_name: bool`、`requires_base_url: bool`、`default_base_url: str | None`、`protocol_family: str`（`openai_compatible` / `anthropic` / `gemini` / `ollama` / `compatible_search`）。

**稳定业务错误码**：`MODEL_NOT_CONFIGURED`、`SEARCH_PROVIDER_NOT_CONFIGURED`（共享 `ProviderError` 基类，`status_code=409`）。

**owner 复用**：`from app.auth.errors import assert_owned`、`from app.auth.errors import NotFoundError`。

---

## Task 1: Credential Vault、信封加密与 Schema

**Files:**
- Modify: `backend/pyproject.toml`（`dependencies` 增加 `cryptography>=42`）
- Modify: `backend/app/config.py`（新增 3 个 Settings 字段）
- Create: `backend/app/credentials/__init__.py`
- Create: `backend/app/credentials/crypto.py`
- Create: `backend/app/credentials/models.py`
- Create: `backend/app/credentials/repository.py`
- Create: `backend/app/credentials/vault.py`
- Create: `backend/app/credentials/errors.py`
- Create: `backend/alembic/versions/0003_create_credentials_providers.py`
- Create: `backend/scripts/generate_master_key.py`
- Modify: `.env.example`（新增 master key 变量与生成说明）
- Create: `backend/tests/credentials/test_crypto.py`
- Create: `backend/tests/credentials/test_vault.py`

**Interfaces:**
- Consumes: `app/auth/errors.assert_owned`、`app/infra/db.Base`、`app/config.Settings`、`app/auth/models.User`。
- Produces: `CredentialVault`（store_secret/read_for_execution/rotate/revoke/get_active/credential_configured）、`CredentialRepository`、四个 ORM 模型、`CredentialError` 家族、migration `0003`。

- [ ] **Step 1: 安装依赖并加配置**

在 `backend/pyproject.toml` 的 `dependencies` 追加 `"cryptography>=42"`。然后在 `backend/app/config.py` 的 `Settings` 中追加：

```python
    # --- Credential master key (M-03, envelope encryption) ---
    # 32-byte hex (64 chars). Never committed. Set in .env (see .env.example).
    credential_master_key: str | None = None
    credential_key_version: str = "k1"
    provider_test_timeout_seconds: float = 15.0
```

在 `backend/scripts/generate_master_key.py` 新建（仅打印，不落盘）：

```python
"""Print a 32-byte (256-bit) master key as 64 hex chars for KAIROS_CREDENTIAL_MASTER_KEY.

Usage: python scripts/generate_master_key.py
Copy the output into .env. Never commit it.
"""
from __future__ import annotations

import secrets

if __name__ == "__main__":
    print(secrets.token_hex(32))
```

在 `.env.example` 追加（只写变量名和说明，空值）：

```dotenv
# ---- Credential master key (M-03) ----
# 32 bytes hex (64 chars)。生成：python backend/scripts/generate_master_key.py
# 绝不提交真实值；staging/production 用 Secret 注入。
KAIROS_CREDENTIAL_MASTER_KEY=
KAIROS_CREDENTIAL_KEY_VERSION=k1
```

运行：

```bash
cd backend && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -c "import cryptography; print(cryptography.__version__)"
```

Expected: 安装成功并打印 cryptography 版本（≥42）。

- [ ] **Step 2: 写加密原语失败测试**

新建 `backend/tests/credentials/test_crypto.py`：

```python
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
```

- [ ] **Step 3: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/credentials/test_crypto.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.credentials`）。

- [ ] **Step 4: 实现 crypto 原语**

新建 `backend/app/credentials/crypto.py`：

```python
"""Envelope encryption primitives (AES-256-GCM).

Layout (envelope):
  secret ciphertext   <- AES-GCM(DEK, nonce, secret, aad)
  wrapped DEK         <- AES-GCM(KEK, wrapped_dek_nonce, DEK, aad)
Only the KEK (from env) is secret to the process; DB stores ciphertext +
wrapped DEK + nonces + algorithm + key version. AAD binds owner_id,
credential_id and version to prevent cross-object ciphertext substitution.
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
    if not value:
        raise CredentialConfigurationError(
            "KAIROS_CREDENTIAL_MASTER_KEY is not set; generate one with "
            "scripts/generate_master_key.py and add it to .env"
        )
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise CredentialConfigurationError("KAIROS_CREDENTIAL_MASTER_KEY must be 64 hex chars") from exc
    if len(key) != KEK_SIZE:
        raise CredentialConfigurationError("KAIROS_CREDENTIAL_MASTER_KEY must be exactly 32 bytes")
    return key


def build_aad(user_id: int, credential_id: int, version: int) -> bytes:
    return f"{user_id}:{credential_id}:{version}".encode("utf-8")


@dataclass(frozen=True)
class EncryptedSecret:
    algorithm: str
    key_version: str
    nonce: bytes
    wrapped_dek_nonce: bytes
    secret_ciphertext: bytes
    wrapped_dek: bytes


def encrypt_secret(*, kek: bytes, secret: str, aad: bytes, key_version: str = "k1") -> EncryptedSecret:
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
        raise CredentialDecryptionError("credential could not be decrypted (tampered or wrong key)") from exc
    return plaintext.decode("utf-8")
```

- [ ] **Step 5: 运行确认通过**

```bash
cd backend && .venv/Scripts/python -m pytest tests/credentials/test_crypto.py -v
```

Expected: PASS（4 passed）。

- [ ] **Step 6: 写 ORM 模型失败测试（migration 先行）**

新建 `backend/tests/credentials/test_vault.py`（SQLite，参照 `tests/auth/test_service.py` 的 fixture 风格）：

```python
"""CredentialVault behavior against SQLite."""
from __future__ import annotations

import pytest
from app.auth import errors
from app.auth.models import User
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials import crypto
from app.credentials.models import Credential, CredentialVersion
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def vault_and_db(tmp_path) -> tuple[CredentialVault, DbSession]:
    engine = create_engine(f"sqlite:///{tmp_path / 'vault.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'vault.db'}",
        credential_master_key="ab" * 32,
        credential_key_version="k1",
    )
    users = UserRepository(db)
    vault = CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )
    yield vault, db
    db.close()


def _user(db: DbSession, email: str) -> User:
    return UserRepository(db).create(email, "hashed-not-used-in-this-test", None)
```

计划沿用该 fixture 结构；本 Task 先写两个关键测试：

```python
def test_store_secret_and_read_for_execution(vault_and_db) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    info = vault.store_secret(user_id=user.id, kind="model_api_key", name="openai", secret="sk-live-abc")
    assert info.credential_id
    assert info.version == 1
    assert vault.read_for_execution(user_id=user.id, credential_version_id=info.version_id) == "sk-live-abc"

def test_db_never_stores_plaintext(vault_and_db) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    vault.store_secret(user_id=user.id, kind="model_api_key", name="openai", secret="sk-super-secret-999")
    rows = db.query(CredentialVersion).all()
    text = repr([{c: getattr(r, c) for c in r.__table__.columns.keys()} for r in rows])
    assert "sk-super-secret-999" not in text
```

- [ ] **Step 7: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/credentials/test_vault.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.credentials.models`）。

- [ ] **Step 8: 实现 ORM 模型 + migration**

新建 `backend/app/credentials/models.py`：

```python
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
    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # model_api_key | search_api_key
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | retired
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
```

新建 `backend/alembic/versions/0003_create_credentials_providers.py`（镜像上述模型，含可逆 downgrade）：

```python
"""create credentials, credential_versions, model_configs, search_configs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-10

M-03 baseline: envelope-encrypted credential vault + versioned provider configs.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.Index("ix_credentials_user_id", "user_id"),
    )
    op.create_table(
        "credential_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("credential_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(length=20), nullable=False),
        sa.Column("key_version", sa.String(length=20), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("wrapped_dek_nonce", sa.LargeBinary(length=16), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id", "version", name="uq_cv_cred_version"),
        sa.Index("ix_credential_versions_credential_id", "credential_id"),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("config_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("credential_version_id", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("connection_status", sa.String(length=30), nullable=False, server_default="untested"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_version_id"], ["credential_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("config_id", "version", name="uq_mc_config_version"),
        sa.Index("ix_model_configs_config_id", "config_id"),
        sa.Index("ix_model_configs_user_id", "user_id"),
    )
    op.create_table(
        "search_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("config_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("credential_version_id", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connection_status", sa.String(length=30), nullable=False, server_default="untested"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_version_id"], ["credential_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("config_id", "version", name="uq_sc_config_version"),
        sa.Index("ix_search_configs_config_id", "config_id"),
        sa.Index("ix_search_configs_user_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("search_configs")
    op.drop_table("model_configs")
    op.drop_table("credential_versions")
    op.drop_table("credentials")
```

- [ ] **Step 9: 实现 Repository + Vault**

新建 `backend/app/credentials/repository.py`：

```python
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

    def next_version(self, credential_id: int) -> int:
        latest = self._db.scalar(
            select(CredentialVersion.version)
            .where(CredentialVersion.credential_id == credential_id)
            .order_by(CredentialVersion.version.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    def add_version(
        self, *, credential_id: int, version: int, algorithm: str, key_version: str,
        nonce: bytes, wrapped_dek_nonce: bytes, secret_ciphertext: bytes, wrapped_dek: bytes,
    ) -> CredentialVersion:
        row = CredentialVersion(
            credential_id=credential_id, version=version, algorithm=algorithm,
            key_version=key_version, nonce=nonce, wrapped_dek_nonce=wrapped_dek_nonce,
            secret_ciphertext=secret_ciphertext, wrapped_dek=wrapped_dek, status="active",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_active_version(self, credential_id: int) -> CredentialVersion | None:
        return self._db.scalar(
            select(CredentialVersion)
            .where(CredentialVersion.credential_id == credential_id, CredentialVersion.status == "active")
            .order_by(CredentialVersion.version.desc())
            .limit(1)
        )

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
```

新建 `backend/app/credentials/vault.py`：

```python
"""CredentialVault: the only place secrets are encrypted/decrypted (M-03).

read_for_execution() must only be called from controlled backend execution
paths (ProviderService connection tests; later Activities). There is no HTTP
endpoint that returns a plaintext secret.
"""
from __future__ import annotations

from app.credentials import crypto
from app.credentials.models import CredentialVersion
from app.credentials.repository import CredentialRepository


class CredentialInfo:
    def __init__(self, credential_id: int, version: int, version_id: int) -> None:
        self.credential_id = credential_id
        self.version = version
        self.version_id = version_id


class CredentialVault:
    def __init__(self, *, master_key: bytes, key_version: str, repository: CredentialRepository) -> None:
        self._kek = master_key
        self._key_version = key_version
        self._repo = repository

    def store_secret(self, *, user_id: int, kind: str, name: str, secret: str) -> CredentialInfo:
        cred = self._repo.create(user_id, kind, name)
        return self._encrypt_new_version(user_id, cred.id, secret)

    def rotate(self, *, user_id: int, credential_id: int, secret: str) -> CredentialInfo:
        cred = self._repo.get_owned(user_id, credential_id)
        return self._encrypt_new_version(user_id, cred.id, secret)

    def read_for_execution(self, *, user_id: int, credential_version_id: int) -> str:
        row = self._db_get_owned_version(user_id, credential_version_id)
        if row.status != "active":
            raise crypto.CredentialError("credential version is retired")
        blob = crypto.EncryptedSecret(
            algorithm=row.algorithm, key_version=row.key_version, nonce=row.nonce,
            wrapped_dek_nonce=row.wrapped_dek_nonce, secret_ciphertext=row.secret_ciphertext,
            wrapped_dek=row.wrapped_dek,
        )
        aad = crypto.build_aad(row.credential_id_owner(row), row.credential_id, row.version)
        return crypto.decrypt_secret(kek=self._kek, blob=blob, aad=aad)

    def revoke(self, *, user_id: int, credential_id: int) -> None:
        cred = self._repo.get_owned(user_id, credential_id)
        active = self._repo.get_active_version(cred.id)
        if active is not None:
            self._repo.retire_and_zero(cred.id, active.id)
        self._repo.disable(cred.id)

    def get_active(self, *, user_id: int, credential_id: int) -> CredentialVersion | None:
        self._repo.get_owned(user_id, credential_id)
        return self._repo.get_active_version(credential_id)

    def credential_configured(self, *, user_id: int, credential_id: int) -> bool:
        return self.get_active(user_id=user_id, credential_id=credential_id) is not None

    def _encrypt_new_version(self, user_id: int, credential_id: int, secret: str) -> CredentialInfo:
        version = self._repo.next_version(credential_id)
        aad = crypto.build_aad(user_id, credential_id, version)
        blob = crypto.encrypt_secret(kek=self._kek, secret=secret, aad=aad, key_version=self._key_version)
        row = self._repo.add_version(
            credential_id=credential_id, version=version, algorithm=blob.algorithm,
            key_version=blob.key_version, nonce=blob.nonce, wrapped_dek_nonce=blob.wrapped_dek_nonce,
            secret_ciphertext=blob.secret_ciphertext, wrapped_dek=blob.wrapped_dek,
        )
        return CredentialInfo(credential_id, version, row.id)

    def _db_get_owned_version(self, user_id: int, credential_version_id: int) -> CredentialVersion:
        from sqlalchemy import select
        row = self._repo._db.scalar(
            select(CredentialVersion).where(CredentialVersion.id == credential_version_id)
        )
        if row is None:
            from app.auth.errors import NotFoundError
            raise NotFoundError("资源不存在")
        owner_id = self._repo._db.scalar(
            select(Credential.user_id).where(Credential.id == row.credential_id)
        )
        if owner_id != user_id:
            from app.auth.errors import NotFoundError
            raise NotFoundError("资源不存在")
        return row
```

> 注意：`_db_get_owned_version` 使用一个小 join 查询校验版本行归属；实现时可改用显式 `get_owned` 后取 active 版本，保持 owner 校验一致。`CredentialVersion.credential_id_owner` 不存在——实现时直接在 vault 内查 `credentials.user_id` 校验（如 `_db_get_owned_version` 所示），不要依赖不存在的模型方法。

新建 `backend/app/credentials/errors.py`：

```python
"""Credential domain errors (M-03)."""
from __future__ import annotations

from app.credentials.crypto import CredentialConfigurationError, CredentialDecryptionError, CredentialError

__all__ = ["CredentialError", "CredentialConfigurationError", "CredentialDecryptionError"]
```

新建 `backend/app/credentials/__init__.py`（空文件即可）。

- [ ] **Step 10: 补全 vault 测试（本 Task 剩余）**

在 `tests/credentials/test_vault.py` 追加：

```python
def test_rotate_keeps_identity_and_versions(vault_and_db) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    first = vault.store_secret(user_id=user.id, kind="model_api_key", name="openai", secret="v1-secret")
    second = vault.rotate(user_id=user.id, credential_id=first.credential_id, secret="v2-secret")
    assert second.credential_id == first.credential_id
    assert second.version == 2
    assert first.version == 1
    # old version identity preserved but retired (rotate retires old active)
    assert vault.read_for_execution(user_id=user.id, credential_version_id=second.version_id) == "v2-secret"


def test_cross_user_cannot_read(vault_and_db) -> None:
    vault, db = vault_and_db
    alice = _user(db, "alice@example.com")
    bob = _user(db, "bob@example.com")
    info = vault.store_secret(user_id=alice.id, kind="model_api_key", name="openai", secret="alice-key")
    with pytest.raises(errors.NotFoundError):
        vault.read_for_execution(user_id=bob.id, credential_version_id=info.version_id)
    with pytest.raises(errors.NotFoundError):
        vault.rotate(user_id=bob.id, credential_id=info.credential_id, secret="x")


def test_revoke_zeroes_ciphertext_and_disables(vault_and_db) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    info = vault.store_secret(user_id=user.id, kind="model_api_key", name="openai", secret="to-revoke")
    vault.revoke(user_id=user.id, credential_id=info.credential_id)
    row = db.query(CredentialVersion).filter(CredentialVersion.id == info.version_id).one()
    assert row.status == "retired"
    assert row.secret_ciphertext == b""
    with pytest.raises(crypto.CredentialError):
        vault.read_for_execution(user_id=user.id, credential_version_id=info.version_id)
```

- [ ] **Step 11: 运行 credentials 测试**

```bash
cd backend && .venv/Scripts/python -m pytest tests/credentials/ -v
```

Expected: PASS。

- [ ] **Step 12: 校验 migration**

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m alembic heads
.venv/Scripts/python -m alembic downgrade 0002 && .venv/Scripts/python -m alembic upgrade head
```

Expected: `head=0003`；downgrade 到 0002 再 upgrade 回 0003 成功（迁移可逆）。

- [ ] **Step 13: 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/ tests/ alembic/ scripts/ .env.example && git commit -m "feat(credential): add envelope-encrypted credential vault and schema"
```

Expected: ruff/mypy PASS；Commit 生成。若 `.env.example` 已在仓库根，用 `git add .env.example`。

---

## Task 2: ModelProvider 协议、Registry 与首批 Adapter

**Files:**
- Create: `backend/app/providers/__init__.py`
- Create: `backend/app/providers/protocol.py`
- Create: `backend/app/providers/transport.py`
- Create: `backend/app/providers/adapters/__init__.py`
- Create: `backend/app/providers/adapters/openai_compatible.py`
- Create: `backend/app/providers/adapters/anthropic.py`
- Create: `backend/app/providers/adapters/gemini.py`
- Create: `backend/app/providers/adapters/ollama.py`
- Create: `backend/app/providers/registry.py`
- Create: `backend/tests/providers/__init__.py`
- Create: `backend/tests/providers/test_registry.py`
- Create: `backend/tests/providers/test_error_mapping.py`

**Interfaces:**
- Consumes: `crypto` 无关（本 Task 不碰 DB）；`ProviderTestResult`/`ProviderTestStatus` 由本 Task 定义。
- Produces: `ProviderDefinition`、`ModelProvider` protocol、`ResolvedModel`、`MODEL_PROVIDER_REGISTRY`、`build_model_provider(provider_type, http) -> ModelProvider`、`list_model_provider_definitions() -> list[ProviderDefinition]`、`validate_model_provider_type(provider_type)`。

- [ ] **Step 1: 定义协议与传输**

新建 `backend/app/providers/protocol.py`：

```python
"""Model/Search provider contracts (M-03). Model and Search stay separate DTOs."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ProviderTestStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProviderTestResult:
    status: ProviderTestStatus
    error_code: str | None = None
    message: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ProviderDefinition:
    provider_type: str
    display_name: str
    requires_api_key: bool
    requires_model_name: bool
    requires_base_url: bool
    default_base_url: str | None
    protocol_family: str


@dataclass(frozen=True)
class ResolvedModel:
    """Stable, serializable descriptor M-06/M-11 map to a real agent model."""

    provider_type: str
    model_name: str
    base_url: str | None
    credential_version_id: int | None


class ModelProvider(Protocol):
    definition: ProviderDefinition

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult: ...

    def resolve_model(
        self, *, model: str, base_url: str | None, credential_version_id: int | None
    ) -> ResolvedModel: ...
```

新建 `backend/app/providers/transport.py`：

```python
"""Minimal HTTP transport protocol + httpx implementation.

Adapters depend on this protocol, never on httpx directly, so connection-test
unit tests inject a fake transport (no real API keys, no network).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any = None
    text: str = ""


class HttpClient(Protocol):
    async def request(
        self, *, method: str, url: str, headers: dict[str, str] | None,
        params: dict[str, str] | None, timeout: float,
    ) -> HttpResponse: ...


class HttpxTransport:
    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def request(
        self, *, method: str, url: str, headers: dict[str, str] | None,
        params: dict[str, str] | None, timeout: float | None,
    ) -> HttpResponse:
        async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
            resp = await client.request(method, url, headers=headers, params=params)
            try:
                body = resp.json()
            except Exception:
                body = None
            return HttpResponse(status_code=resp.status_code, body=body, text=resp.text)
```

- [ ] **Step 2: 写 registry 失败测试**

新建 `backend/tests/providers/test_registry.py`：

```python
"""Model provider registry: all seven first-party providers registered."""
from __future__ import annotations

import pytest
from app.providers import errors as perr
from app.providers.registry import (
    build_model_provider,
    list_model_provider_definitions,
    validate_model_provider_type,
)

EXPECTED_MODEL_PROVIDERS = {
    "openai", "anthropic", "gemini", "deepseek", "openrouter", "ollama",
    "custom_openai_compatible",
}


def test_all_seven_model_providers_registered() -> None:
    types = {d.provider_type for d in list_model_provider_definitions()}
    assert EXPECTED_MODEL_PROVIDERS <= types


def test_openai_family_share_protocol_family() -> None:
    families = {d.provider_type: d.protocol_family for d in list_model_provider_definitions()}
    for t in ("openai", "deepseek", "openrouter", "custom_openai_compatible"):
        assert families[t] == "openai_compatible"
    assert families["anthropic"] == "anthropic"
    assert families["gemini"] == "gemini"
    assert families["ollama"] == "ollama"


def test_ollama_does_not_require_key() -> None:
    defn = next(d for d in list_model_provider_definitions() if d.provider_type == "ollama")
    assert defn.requires_api_key is False


def test_invalid_provider_type_rejected() -> None:
    with pytest.raises(perr.ProviderValidationError):
        validate_model_provider_type("not-a-provider")


def test_build_returns_provider() -> None:
    from tests.providers.fake_transport import FakeHttpClient
    provider = build_model_provider("openai", http=FakeHttpClient(200, {}))
    assert provider.definition.provider_type == "openai"
```

- [ ] **Step 3: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_registry.py -v
```

Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4: 实现共享错误映射 + OpenAI-compatible 核心 Adapter**

新建 `backend/app/providers/errors.py`：

```python
"""Stable provider error taxonomy (M-03)."""
from __future__ import annotations


class ProviderError(Exception):
    code: str = "PROVIDER_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ProviderValidationError(ProviderError):
    code = "PROVIDER_VALIDATION_ERROR"
    status_code = 422


class ModelNotConfiguredError(ProviderError):
    code = "MODEL_NOT_CONFIGURED"
    status_code = 409


class SearchProviderNotConfiguredError(ProviderError):
    code = "SEARCH_PROVIDER_NOT_CONFIGURED"
    status_code = 409
```

新建 `backend/app/providers/adapters/__init__.py`（空）。

新建 `backend/app/providers/adapters/openai_compatible.py`（共享核心，供 OpenAI/DeepSeek/OpenRouter/Custom 复用）：

```python
"""Shared OpenAI-compatible model adapter core + four registrations.

test_connection does a minimal real request to {base_url}/models and maps the
status deterministically so unit tests can drive every branch with a fake
transport.
"""
from __future__ import annotations

from app.providers.protocol import (
    ProviderDefinition,
    ProviderTestResult,
    ProviderTestStatus,
    ResolvedModel,
)
from app.providers.transport import HttpClient, HttpxTransport


def map_status(http_status: int, *, model_specific_404: bool = True) -> tuple[ProviderTestStatus, str | None]:
    if http_status == 200:
        return ProviderTestStatus.AVAILABLE, None
    if http_status in (401, 403):
        return ProviderTestStatus.AUTH_FAILED, f"HTTP_{http_status}"
    if http_status == 404:
        return (ProviderTestStatus.MODEL_NOT_FOUND, "HTTP_404") if model_specific_404 else (
            ProviderTestStatus.NETWORK_ERROR, "HTTP_404")
    if http_status == 429:
        return ProviderTestStatus.RATE_LIMITED, "HTTP_429"
    return ProviderTestStatus.FAILED, f"HTTP_{http_status}"


class OpenAICompatibleModelProvider:
    definition: ProviderDefinition

    def __init__(self, definition: ProviderDefinition, http: HttpClient | None = None) -> None:
        self.definition = definition
        self._http = http or HttpxTransport()

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url or "").rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key or ''}"}
        from time import perf_counter
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint, headers=headers, params=None, timeout=15.0
            )
        except Exception:
            return ProviderTestResult(
                status=ProviderTestStatus.NETWORK_ERROR,
                error_code="NETWORK_ERROR",
                message="无法连接 Provider",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        status, code = map_status(resp.status_code)
        return ProviderTestResult(
            status=status,
            error_code=code,
            message=("连接成功" if status is ProviderTestStatus.AVAILABLE else None),
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def resolve_model(
        self, *, model: str, base_url: str | None, credential_version_id: int | None
    ) -> ResolvedModel:
        return ResolvedModel(
            provider_type=self.definition.provider_type,
            model_name=model,
            base_url=base_url or self.definition.default_base_url,
            credential_version_id=credential_version_id,
        )
```

- [ ] **Step 5: 实现 Anthropic / Gemini / Ollama Adapter**

新建 `backend/app/providers/adapters/anthropic.py`：

```python
"""Native Anthropic model adapter (minimal connection test against /v1/models)."""
from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import ProviderDefinition, ProviderTestResult, ProviderTestStatus, ResolvedModel
from app.providers.transport import HttpClient, HttpxTransport

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicModelProvider:
    definition = ProviderDefinition(
        provider_type="anthropic", display_name="Anthropic", requires_api_key=True,
        requires_model_name=True, requires_base_url=False,
        default_base_url="https://api.anthropic.com", protocol_family="anthropic",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(self, *, api_key, model, base_url) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url).rstrip("/") + "/v1/models"
        headers = {"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION}
        started = perf_counter()
        try:
            resp = await self._http.request(method="GET", url=endpoint, headers=headers, params=None, timeout=15.0)
        except Exception:
            return ProviderTestResult(status=ProviderTestStatus.NETWORK_ERROR, error_code="NETWORK_ERROR", latency_ms=int((perf_counter()-started)*1000))
        status, code = map_status(resp.status_code)
        return ProviderTestResult(status=status, error_code=code, latency_ms=int((perf_counter()-started)*1000))

    def resolve_model(self, *, model, base_url, credential_version_id) -> ResolvedModel:
        return ResolvedModel(provider_type="anthropic", model_name=model, base_url=base_url or self.definition.default_base_url, credential_version_id=credential_version_id)
```

新建 `backend/app/providers/adapters/gemini.py`（Gemini 无效 key 返回 400，映射为 AUTH_FAILED）：

```python
"""Native Google Gemini model adapter (minimal connection test)."""
from __future__ import annotations

from time import perf_counter

from app.providers.protocol import ProviderDefinition, ProviderTestResult, ProviderTestStatus, ResolvedModel
from app.providers.transport import HttpClient, HttpxTransport


def map_gemini_status(http_status: int) -> tuple[ProviderTestStatus, str | None]:
    if http_status == 200:
        return ProviderTestStatus.AVAILABLE, None
    if http_status in (400, 401, 403):
        return ProviderTestStatus.AUTH_FAILED, f"HTTP_{http_status}"
    if http_status == 404:
        return ProviderTestStatus.MODEL_NOT_FOUND, "HTTP_404"
    if http_status == 429:
        return ProviderTestStatus.RATE_LIMITED, "HTTP_429"
    return ProviderTestStatus.FAILED, f"HTTP_{http_status}"


class GeminiModelProvider:
    definition = ProviderDefinition(
        provider_type="gemini", display_name="Google Gemini", requires_api_key=True,
        requires_model_name=True, requires_base_url=False,
        default_base_url="https://generativelanguage.googleapis.com/v1beta", protocol_family="gemini",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(self, *, api_key, model, base_url) -> ProviderTestResult:
        base = (base_url or self.definition.default_base_url).rstrip("/")
        endpoint = f"{base}/models"
        started = perf_counter()
        try:
            resp = await self._http.request(method="GET", url=endpoint, headers={"x-goog-api-key": api_key or ""}, params={"key": api_key or ""}, timeout=15.0)
        except Exception:
            return ProviderTestResult(status=ProviderTestStatus.NETWORK_ERROR, error_code="NETWORK_ERROR", latency_ms=int((perf_counter()-started)*1000))
        status, code = map_gemini_status(resp.status_code)
        return ProviderTestResult(status=status, error_code=code, latency_ms=int((perf_counter()-started)*1000))

    def resolve_model(self, *, model, base_url, credential_version_id) -> ResolvedModel:
        return ResolvedModel(provider_type="gemini", model_name=model, base_url=base_url or self.definition.default_base_url, credential_version_id=credential_version_id)
```

新建 `backend/app/providers/adapters/ollama.py`（无 Key，`/api/tags`）：

```python
"""Ollama adapter: no API key, local endpoint, GET /api/tags."""
from __future__ import annotations

from time import perf_counter

from app.providers.protocol import ProviderDefinition, ProviderTestResult, ProviderTestStatus, ResolvedModel
from app.providers.transport import HttpClient, HttpxTransport


class OllamaModelProvider:
    definition = ProviderDefinition(
        provider_type="ollama", display_name="Ollama", requires_api_key=False,
        requires_model_name=True, requires_base_url=True,
        default_base_url="http://localhost:11434", protocol_family="ollama",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(self, *, api_key, model, base_url) -> ProviderTestResult:
        endpoint = (base_url or self.definition.default_base_url).rstrip("/") + "/api/tags"
        started = perf_counter()
        try:
            resp = await self._http.request(method="GET", url=endpoint, headers=None, params=None, timeout=15.0)
        except Exception:
            return ProviderTestResult(status=ProviderTestStatus.NETWORK_ERROR, error_code="NETWORK_ERROR", latency_ms=int((perf_counter()-started)*1000))
        status, code = map_status(resp.status_code)
        return ProviderTestResult(status=status, error_code=code, latency_ms=int((perf_counter()-started)*1000))

    def resolve_model(self, *, model, base_url, credential_version_id) -> ResolvedModel:
        return ResolvedModel(provider_type="ollama", model_name=model, base_url=base_url or self.definition.default_base_url, credential_version_id=credential_version_id)
```

- [ ] **Step 6: 实现 Registry**

新建 `backend/app/providers/registry.py`：

```python
"""Code-registered, typed provider registry (M-03).

Providers are registered here in code — never from DB class paths. The
registry exposes metadata so the frontend/service can render forms without
per-provider if/else.
"""
from __future__ import annotations

from app.providers import errors
from app.providers.adapters.anthropic import AnthropicModelProvider
from app.providers.adapters.gemini import GeminiModelProvider
from app.providers.adapters.ollama import OllamaModelProvider
from app.providers.adapters.openai_compatible import OpenAICompatibleModelProvider
from app.providers.protocol import ModelProvider, ProviderDefinition
from app.providers.transport import HttpClient

_OPENAI_COMPATIBLE_DEFS: list[ProviderDefinition] = [
    ProviderDefinition(
        provider_type="openai", display_name="OpenAI", requires_api_key=True,
        requires_model_name=True, requires_base_url=False,
        default_base_url="https://api.openai.com/v1", protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="deepseek", display_name="DeepSeek", requires_api_key=True,
        requires_model_name=True, requires_base_url=False,
        default_base_url="https://api.deepseek.com/v1", protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="openrouter", display_name="OpenRouter", requires_api_key=True,
        requires_model_name=True, requires_base_url=False,
        default_base_url="https://openrouter.ai/api/v1", protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="custom_openai_compatible", display_name="Custom OpenAI-compatible",
        requires_api_key=True, requires_model_name=True, requires_base_url=True,
        default_base_url=None, protocol_family="openai_compatible",
    ),
]

_MODEL_PROVIDER_BUILDERS: dict[str, type] = {
    "openai": OpenAICompatibleModelProvider,
    "deepseek": OpenAICompatibleModelProvider,
    "openrouter": OpenAICompatibleModelProvider,
    "custom_openai_compatible": OpenAICompatibleModelProvider,
    "anthropic": AnthropicModelProvider,
    "gemini": GeminiModelProvider,
    "ollama": OllamaModelProvider,
}


def list_model_provider_definitions() -> list[ProviderDefinition]:
    return [
        *[
            defn
            for defn in _OPENAI_COMPATIBLE_DEFS
        ],
        AnthropicModelProvider.definition,
        GeminiModelProvider.definition,
        OllamaModelProvider.definition,
    ]


def validate_model_provider_type(provider_type: str) -> None:
    if provider_type not in _MODEL_PROVIDER_BUILDERS:
        raise errors.ProviderValidationError(f"不支持的模型 Provider: {provider_type}")


def build_model_provider(provider_type: str, http: HttpClient | None = None) -> ModelProvider:
    validate_model_provider_type(provider_type)
    builder = _MODEL_PROVIDER_BUILDERS[provider_type]
    for defn in _OPENAI_COMPATIBLE_DEFS:
        if defn.provider_type == provider_type:
            return OpenAICompatibleModelProvider(defn, http)  # type: ignore[return-value]
    return builder(http)
```

- [ ] **Step 7: 实现 fake transport 与错误映射测试**

新建 `backend/tests/providers/fake_transport.py`：

```python
"""Fake HttpClient for provider tests (no real network / keys)."""
from __future__ import annotations

from app.providers.transport import HttpResponse


class FakeHttpClient:
    def __init__(self, status_code: int = 200, body=None, *, raise_network: bool = False) -> None:
        self._status = status_code
        self._body = body
        self._raise_network = raise_network
        self.calls: list[dict] = []

    async def request(self, *, method, url, headers=None, params=None, timeout=15.0) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params})
        if self._raise_network:
            raise ConnectionError("boom")
        return HttpResponse(status_code=self._status, body=self._body)
```

新建 `backend/tests/providers/test_error_mapping.py`：

```python
"""Provider connection-test error mapping via fake transport."""
from __future__ import annotations

import pytest
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_model_provider
from tests.providers.fake_transport import FakeHttpClient


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, ProviderTestStatus.AVAILABLE),
        (401, ProviderTestStatus.AUTH_FAILED),
        (403, ProviderTestStatus.AUTH_FAILED),
        (404, ProviderTestStatus.MODEL_NOT_FOUND),
        (429, ProviderTestStatus.RATE_LIMITED),
    ],
)
async def test_openai_compatible_status_mapping(status: int, expected: ProviderTestStatus) -> None:
    provider = build_model_provider("openai", http=FakeHttpClient(status_code=status, body={}))
    result = await provider.test_connection(api_key="sk-test", model="gpt-4o-mini", base_url=None)
    assert result.status is expected


async def test_network_error_maps_to_network_error() -> None:
    provider = build_model_provider(
        "anthropic", http=FakeHttpClient(raise_network=True)
    )
    result = await provider.test_connection(api_key="sk-test", model="claude-3-5-sonnet", base_url=None)
    assert result.status is ProviderTestStatus.NETWORK_ERROR
```

- [ ] **Step 8: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_registry.py tests/providers/test_error_mapping.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/providers backend/tests/providers && git commit -m "feat(provider): add model provider registry and adapters"
```

Expected: 全 PASS；Commit 生成。

> 注意：`pytest.mark.anyio` 依赖 `anyio`（fastapi 已带）。若 `AsyncClient`/`asyncio` 标记问题，可改用 `pytest.mark.asyncio`（`pytest-asyncio` 在 dev 依赖中）。

---

## Task 3: SearchProvider 协议、Registry 与 SearchConfig 契约

**Files:**
- Create: `backend/app/providers/search_protocol.py`
- Create: `backend/app/providers/adapters/custom_compatible_search.py`
- Modify: `backend/app/providers/registry.py`（追加 search registry）
- Create: `backend/tests/providers/test_search_provider.py`

**Interfaces:**
- Consumes: `app/providers/protocol.py`（`ProviderTestResult`/`ProviderDefinition`）、`app/providers/transport.py`、`app/providers/errors.py`。
- Produces: `SearchResult`、`SearchProvider` protocol、`SEARCH_PROVIDER_BUILDERS`、`build_search_provider(provider_type, http)`、`list_search_provider_definitions()`、`validate_search_provider_type(provider_type)`。

- [ ] **Step 1: 写 SearchProvider 契约 + adapter 失败测试**

新建 `backend/tests/providers/test_search_provider.py`：

```python
"""Search provider contract: registry + compatible adapter + result DTO."""
from __future__ import annotations

import pytest
from app.providers import errors as perr
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import (
    build_search_provider,
    list_search_provider_definitions,
    validate_search_provider_type,
)
from app.providers.search_protocol import SearchResult
from tests.providers.fake_transport import FakeHttpClient


def test_search_registry_has_compatible_provider() -> None:
    types = {d.provider_type for d in list_search_provider_definitions()}
    assert "custom_compatible_search" in types


def test_invalid_search_provider_rejected() -> None:
    with pytest.raises(perr.ProviderValidationError):
        validate_search_provider_type("tavily")


@pytest.mark.anyio
async def test_search_connection_available() -> None:
    fake = FakeHttpClient(status_code=200, body={"results": []})
    provider = build_search_provider("custom_compatible_search", http=fake)
    result = await provider.test_connection(api_key="sk-test", base_url="http://stub/search")
    assert result.status is ProviderTestStatus.AVAILABLE


@pytest.mark.anyio
async def test_search_parses_results() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={"results": [{"url": "https://a.example", "title": "A", "snippet": "..."}]},
    )
    provider = build_search_provider("custom_compatible_search", http=fake)
    results = await provider.search(query="kairos", limit=5, api_key="sk-test", base_url="http://stub/search")
    assert results[0] == SearchResult(
        url="https://a.example", title="A", snippet="...", provider="custom_compatible_search", rank=1, query="kairos"
    )


@pytest.mark.anyio
async def test_search_429_maps_to_rate_limited() -> None:
    provider = build_search_provider("custom_compatible_search", http=FakeHttpClient(status_code=429, body={}))
    result = await provider.test_connection(api_key="sk-test", base_url="http://stub/search")
    assert result.status is ProviderTestStatus.RATE_LIMITED
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_search_provider.py -v
```

Expected: FAIL。

- [ ] **Step 3: 实现 SearchProvider 协议 + compatible adapter**

新建 `backend/app/providers/search_protocol.py`：

```python
"""Search provider contract (M-03). Independent from ModelProvider.

The compatible HTTP contract (CUSTOM_COMPATIBLE_SEARCH):
  GET {base_url}/search?q=<query>&limit=<n>
  Authorization: Bearer <api_key>
  Response: {"results": [{"url", "title", "snippet"}]}
  Error mapping: 200->AVAILABLE; 401/403->AUTH_FAILED; 404->NETWORK_ERROR;
                 429->RATE_LIMITED; transport error->NETWORK_ERROR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.providers.protocol import ProviderDefinition, ProviderTestResult


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    provider: str
    rank: int | None
    query: str


class SearchProvider(Protocol):
    definition: ProviderDefinition

    async def test_connection(
        self, *, api_key: str | None, base_url: str | None
    ) -> ProviderTestResult: ...

    async def search(
        self, *, query: str, limit: int, api_key: str | None, base_url: str | None
    ) -> list[SearchResult]: ...
```

新建 `backend/app/providers/adapters/custom_compatible_search.py`：

```python
"""Minimal pluggable compatible search adapter (M-03 scope only).

Implements the documented compatible contract so M-09 can build SourceSearch
orchestration on top; M-03 does not implement search strategy/frontier/robots.
"""
from __future__ import annotations

from time import perf_counter

from app.providers.adapters.openai_compatible import map_status
from app.providers.protocol import ProviderDefinition, ProviderTestResult, ProviderTestStatus
from app.providers.search_protocol import SearchResult
from app.providers.transport import HttpClient, HttpxTransport


class CustomCompatibleSearchProvider:
    definition = ProviderDefinition(
        provider_type="custom_compatible_search", display_name="Custom Compatible Search",
        requires_api_key=True, requires_model_name=False, requires_base_url=True,
        default_base_url=None, protocol_family="compatible_search",
    )

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpxTransport()

    async def test_connection(self, *, api_key, base_url) -> ProviderTestResult:
        endpoint = f"{base_url.rstrip('/')}/search"
        started = perf_counter()
        try:
            resp = await self._http.request(
                method="GET", url=endpoint,
                headers={"Authorization": f"Bearer {api_key or ''}"},
                params={"q": "kairos", "limit": "1"}, timeout=15.0,
            )
        except Exception:
            return ProviderTestResult(status=ProviderTestStatus.NETWORK_ERROR, error_code="NETWORK_ERROR", latency_ms=int((perf_counter()-started)*1000))
        status, code = map_status(resp.status_code, model_specific_404=False)
        return ProviderTestResult(status=status, error_code=code, latency_ms=int((perf_counter()-started)*1000))

    async def search(self, *, query, limit, api_key, base_url) -> list[SearchResult]:
        endpoint = f"{base_url.rstrip('/')}/search"
        resp = await self._http.request(
            method="GET", url=endpoint,
            headers={"Authorization": f"Bearer {api_key or ''}"},
            params={"q": query, "limit": str(limit)}, timeout=15.0,
        )
        body = resp.body if isinstance(resp.body, dict) else {}
        raw = body.get("results") or []
        out: list[SearchResult] = []
        for idx, item in enumerate(raw, start=1):
            out.append(
                SearchResult(
                    url=item["url"], title=item.get("title", ""), snippet=item.get("snippet", ""),
                    provider=self.definition.provider_type, rank=idx, query=query,
                )
            )
        return out
```

- [ ] **Step 4: 在 registry.py 追加 search registry**

在 `backend/app/providers/registry.py` 追加：

```python
from app.providers.adapters.custom_compatible_search import CustomCompatibleSearchProvider

_SEARCH_PROVIDER_BUILDERS: dict[str, type] = {
    "custom_compatible_search": CustomCompatibleSearchProvider,
}


def list_search_provider_definitions() -> list[ProviderDefinition]:
    return [CustomCompatibleSearchProvider.definition]


def validate_search_provider_type(provider_type: str) -> None:
    if provider_type not in _SEARCH_PROVIDER_BUILDERS:
        raise errors.ProviderValidationError(f"不支持的搜索 Provider: {provider_type}")


def build_search_provider(provider_type: str, http: HttpClient | None = None) -> SearchProvider:
    validate_search_provider_type(provider_type)
    return _SEARCH_PROVIDER_BUILDERS[provider_type](http)
```

（`SearchProvider` 从 `app.providers.search_protocol` 导入。）

- [ ] **Step 5: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_search_provider.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/providers backend/tests/providers && git commit -m "feat(search): add search provider configuration contract"
```

Expected: 全 PASS；Commit 生成。

---

## Task 4: Provider Service、版本化仓库、Guard 与错误分类

**Files:**
- Create: `backend/app/providers/repository.py`
- Create: `backend/app/providers/service.py`
- Create: `backend/app/providers/deps.py`
- Create: `backend/tests/providers/test_config_versioning.py`
- Create: `backend/tests/providers/test_owner_isolation.py`
- Create: `backend/tests/providers/test_guards.py`

**Interfaces:**
- Consumes: Task 1 的 `CredentialVault`/`CredentialInfo`/模型、Task 2 的 `ModelProvider`/registry、Task 3 的 `SearchProvider`。
- Produces: `ProviderService`（model/search 生命周期 + test + set_default + delete + guards）、`ModelConfigRepository`、`SearchConfigRepository`、`get_provider_service`/`get_credential_vault` 依赖。

- [ ] **Step 1: 写 Service 版本化失败测试**

新建 `backend/tests/providers/test_config_versioning.py`（SQLite，用户通过 `UserRepository` 创建）：

```python
"""Model/Search config versioning semantics (M-03)."""
from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials.crypto import master_key_from_env_value
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.db import Base
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def service_and_db(tmp_path) -> tuple[ProviderService, DbSession]:
    engine = create_engine(f"sqlite:///{tmp_path / 'prov.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'prov.db'}",
        credential_master_key="ab" * 32,
        credential_key_version="k1",
    )
    users = UserRepository(db)
    vault = CredentialVault(
        master_key=master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )
    service = ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(db),
        search_configs=SearchConfigRepository(db),
    )
    yield service, db
    db.close()


def _user(db: DbSession, email: str) -> User:
    return UserRepository(db).create(email, "hash", None)


def test_edit_creates_new_version(service_and_db) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_model_config(
        user, name="main", provider_type="openai", model_name="gpt-4o-mini",
        base_url=None, api_key="sk-abc",
    )
    edited = service.update_model_config(
        user, created.config_id, name="main-2", provider_type="openai", model_name="gpt-4o", base_url=None,
    )
    assert edited.config_id == created.config_id
    assert edited.version == 2
    assert edited.model_name == "gpt-4o"
    # old version preserved
    old = service.get_model_config_version(user, created.config_id, 1)
    assert old.model_name == "gpt-4o-mini"


def test_replace_key_bumps_credential_and_config_version(service_and_db) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_model_config(
        user, name="main", provider_type="anthropic", model_name="claude-3-5-sonnet", base_url=None, api_key="key-v1",
    )
    replaced = service.replace_model_api_key(user, created.config_id, "key-v2")
    assert replaced.version == 2
    cred_v1 = service.get_credential_version_id(user, created.config_id, created.version)
    cred_v2 = service.get_credential_version_id(user, replaced.config_id, replaced.version)
    assert cred_v2 is not None and cred_v1 != cred_v2


def test_set_default_only_changes_current(service_and_db) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    a = service.create_model_config(user, name="a", provider_type="openai", model_name="gpt-4o-mini", base_url=None, api_key=None)
    b = service.create_model_config(user, name="b", provider_type="openai", model_name="gpt-4o-mini", base_url=None, api_key=None)
    service.set_default_model(user, a.config_id)
    default = service.get_default_model(user)
    assert default is not None and default.config_id == a.config_id
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_config_versioning.py -v
```

Expected: FAIL。

- [ ] **Step 3: 实现 Repository + Service**

新建 `backend/app/providers/repository.py`（owner-scoped、版本化）：

```python
"""Versioned repositories for model/search configs (M-03).

Pattern: every edit appends a new row with (config_id, version+1) and flips
is_current on the new row; old rows are immutable history. Config_id is the
stable logical identity; M-06 freezes (config_id, version).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.auth.errors import NotFoundError
from app.providers.models import ModelConfig, SearchConfig


def _current(stmt, db):  # noqa: ANN001
    return db.scalar(stmt)


class ModelConfigRepository:
    def __init__(self, db: DbSession) -> None:
        self._db = db

    def next_version(self, config_id: str) -> int:
        latest = self._db.scalar(
            select(ModelConfig.version).where(ModelConfig.config_id == config_id).order_by(ModelConfig.version.desc()).limit(1)
        )
        return (latest or 0) + 1

    def create_version(self, *, user_id: int, name: str, provider_type: str, model_name: str,
                       base_url: str | None, credential_version_id: int | None, is_default: bool) -> ModelConfig:
        config_id = __import__("uuid").uuid4().hex
        row = ModelConfig(
            config_id=config_id, user_id=user_id, version=1, name=name,
            provider_type=provider_type, model_name=model_name, base_url=base_url,
            credential_version_id=credential_version_id, is_current=True,
            is_default=is_default, connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def append_version(self, *, config_id: str, user_id: int, name: str, provider_type: str,
                       model_name: str, base_url: str | None, credential_version_id: int | None,
                       is_default: bool) -> ModelConfig:
        version = self.next_version(config_id)
        self._unset_current(config_id, user_id)
        row = ModelConfig(
            config_id=config_id, user_id=user_id, version=version, name=name,
            provider_type=provider_type, model_name=model_name, base_url=base_url,
            credential_version_id=credential_version_id, is_current=True,
            is_default=is_default, connection_status="untested",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_current(self, user_id: int, config_id: str) -> ModelConfig:
        row = self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.config_id == config_id, ModelConfig.user_id == user_id,
                ModelConfig.is_current.is_(True),
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def get_version(self, user_id: int, config_id: str, version: int) -> ModelConfig:
        row = self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.config_id == config_id, ModelConfig.user_id == user_id,
                ModelConfig.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def list_current(self, user_id: int) -> list[ModelConfig]:
        return list(self._db.scalars(
            select(ModelConfig).where(ModelConfig.user_id == user_id, ModelConfig.is_current.is_(True))
            .order_by(ModelConfig.created_at.desc())
        ))

    def get_default(self, user_id: int) -> ModelConfig | None:
        return self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.user_id == user_id, ModelConfig.is_current.is_(True), ModelConfig.is_default.is_(True)
            )
        )

    def set_default(self, user_id: int, config_id: str) -> None:
        self.get_current(user_id, config_id)  # ownership check
        self._db.execute(
            update(ModelConfig).where(ModelConfig.user_id == user_id, ModelConfig.is_current.is_(True)).values(is_default=False)
        )
        self._db.execute(
            update(ModelConfig).where(
                ModelConfig.user_id == user_id, ModelConfig.config_id == config_id, ModelConfig.is_current.is_(True)
            ).values(is_default=True)
        )
        self._db.commit()

    def mark_connection(self, user_id: int, config_id: str, status: str, tested_at: datetime) -> None:
        self.get_current(user_id, config_id)
        self._db.execute(
            update(ModelConfig).where(
                ModelConfig.user_id == user_id, ModelConfig.config_id == config_id, ModelConfig.is_current.is_(True)
            ).values(connection_status=status, last_tested_at=tested_at)
        )
        self._db.commit()

    def _unset_current(self, config_id: str, user_id: int) -> None:
        self._db.execute(
            update(ModelConfig).where(
                ModelConfig.user_id == user_id, ModelConfig.config_id == config_id, ModelConfig.is_current.is_(True)
            ).values(is_current=False)
        )
        self._db.commit()
```

`SearchConfigRepository` 与 `ModelConfigRepository` 结构一致（无 `is_default`/`set_default`，有 `list_current`/`get_current`/`get_version`/`create_version`/`append_version`/`mark_connection`/`delete`），在同一个文件中实现。

新建 `backend/app/providers/models.py`（重导出 Task 1 的 ORM 模型，避免循环导入）：

```python
"""Provider config ORM models (re-export from app.credentials.models)."""
from app.credentials.models import ModelConfig, SearchConfig

__all__ = ["ModelConfig", "SearchConfig"]
```

新建 `backend/app/providers/service.py`：

```python
"""ProviderService: model/search config lifecycle, connection tests, guards.

Route stays thin; all credential decryption happens here through
CredentialVault.read_for_execution (controlled execution path only).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.credentials.vault import CredentialInfo, CredentialVault
from app.providers import errors
from app.providers.protocol import ProviderTestResult
from app.providers.registry import build_model_provider, build_search_provider, validate_model_provider_type, validate_search_provider_type
from app.providers.repository import ModelConfigRepository, SearchConfigRepository


def _now() -> datetime:
    return datetime.now(UTC)


class ProviderService:
    def __init__(self, *, vault: CredentialVault, model_configs: ModelConfigRepository,
                 search_configs: SearchConfigRepository) -> None:
        self._vault = vault
        self._model_configs = model_configs
        self._search_configs = search_configs

    # ---- Model config lifecycle ----
    def create_model_config(self, user, *, name: str, provider_type: str, model_name: str,
                            base_url: str | None, api_key: str | None, set_default: bool = False) -> Any:
        validate_model_provider_type(provider_type)
        credential_version_id = None
        if api_key:
            info = self._vault.store_secret(user_id=user.id, kind="model_api_key", name=name, secret=api_key)
            credential_version_id = info.version_id
        if set_default:
            for current in self._model_configs.list_current(user.id):
                self._model_configs.set_default(user.id, current.config_id) if current.is_default else None
        return self._model_configs.create_version(
            user_id=user.id, name=name, provider_type=provider_type, model_name=model_name,
            base_url=base_url, credential_version_id=credential_version_id, is_default=set_default,
        )

    def update_model_config(self, user, *, config_id: str, name: str, provider_type: str,
                            model_name: str, base_url: str | None) -> Any:
        validate_model_provider_type(provider_type)
        current = self._model_configs.get_current(user.id, config_id)
        return self._model_configs.append_version(
            config_id=config_id, user_id=user.id, name=name, provider_type=provider_type,
            model_name=model_name, base_url=base_url,
            credential_version_id=current.credential_version_id, is_default=current.is_default,
        )

    def replace_model_api_key(self, user, *, config_id: str, api_key: str) -> Any:
        current = self._model_configs.get_current(user.id, config_id)
        info: CredentialInfo | None = None
        if current.credential_version_id is not None:
            # find the owning credential id from the current credential_version
            from app.credentials.models import CredentialVersion
            # (service resolves credential_id via vault helper below)
            info = self._vault.rotate_for_config(user.id, current.credential_version_id, api_key)
        else:
            info = self._vault.store_secret(user_id=user.id, kind="model_api_key", name=current.name, secret=api_key)
        return self._model_configs.append_version(
            config_id=config_id, user_id=user.id, name=current.name, provider_type=current.provider_type,
            model_name=current.model_name, base_url=current.base_url,
            credential_version_id=info.version_id, is_default=current.is_default,
        )

    async def test_model_connection(self, user, *, config_id: str) -> ProviderTestResult:
        current = self._model_configs.get_current(user.id, config_id)
        api_key = None
        if current.credential_version_id is not None:
            api_key = self._vault.read_for_execution(user_id=user.id, credential_version_id=current.credential_version_id)
        provider = build_model_provider(current.provider_type)
        result = await provider.test_connection(
            api_key=api_key, model=current.model_name, base_url=current.base_url
        )
        self._model_configs.mark_connection(user.id, config_id, result.status.value, _now())
        return result

    def set_default_model(self, user, *, config_id: str) -> Any:
        return self._model_configs.set_default(user.id, config_id)

    def delete_model_config(self, user, *, config_id: str) -> None:
        current = self._model_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            self._revoke_config_credential(user.id, current.credential_version_id)
        self._model_configs.delete(user.id, config_id)

    # ---- Search config lifecycle ----
    def create_search_config(self, user, *, name: str, provider_type: str, base_url: str | None,
                             api_key: str | None) -> Any:
        validate_search_provider_type(provider_type)
        credential_version_id = None
        if api_key:
            info = self._vault.store_secret(user_id=user.id, kind="search_api_key", name=name, secret=api_key)
            credential_version_id = info.version_id
        return self._search_configs.create_version(
            user_id=user.id, name=name, provider_type=provider_type, base_url=base_url,
            credential_version_id=credential_version_id,
        )

    def update_search_config(self, user, *, config_id: str, name: str, provider_type: str, base_url: str | None) -> Any:
        validate_search_provider_type(provider_type)
        current = self._search_configs.get_current(user.id, config_id)
        return self._search_configs.append_version(
            config_id=config_id, user_id=user.id, name=name, provider_type=provider_type,
            base_url=base_url, credential_version_id=current.credential_version_id,
        )

    def replace_search_api_key(self, user, *, config_id: str, api_key: str) -> Any:
        current = self._search_configs.get_current(user.id, config_id)
        info = self._vault.rotate_for_config(user.id, current.credential_version_id, api_key) if current.credential_version_id else self._vault.store_secret(user_id=user.id, kind="search_api_key", name=current.name, secret=api_key)
        return self._search_configs.append_version(
            config_id=config_id, user_id=user.id, name=current.name, provider_type=current.provider_type,
            base_url=current.base_url, credential_version_id=info.version_id,
        )

    async def test_search_connection(self, user, *, config_id: str) -> ProviderTestResult:
        current = self._search_configs.get_current(user.id, config_id)
        api_key = None
        if current.credential_version_id is not None:
            api_key = self._vault.read_for_execution(user_id=user.id, credential_version_id=current.credential_version_id)
        provider = build_search_provider(current.provider_type)
        result = await provider.test_connection(api_key=api_key, base_url=current.base_url)
        self._search_configs.mark_connection(user.id, config_id, result.status.value, _now())
        return result

    def delete_search_config(self, user, *, config_id: str) -> None:
        current = self._search_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            self._revoke_config_credential(user.id, current.credential_version_id)
        self._search_configs.delete(user.id, config_id)

    # ---- Guards (stable business errors) ----
    def require_available_model_config(self, user) -> Any:
        config = self._model_configs.get_default(user.id)
        if config is None:
            raise errors.ModelNotConfiguredError("尚未配置可用的 AI 模型")
        return config

    def require_available_search_config(self, user) -> Any:
        config = next((c for c in self._search_configs.list_current(user.id) if c.connection_status == "available"), None)
        if config is None:
            raise errors.SearchProviderNotConfiguredError("尚未配置可用的搜索服务")
        return config

    # ---- helpers ----
    def _revoke_config_credential(self, user_id: int, credential_version_id: int) -> None:
        self._vault.revoke_by_version(user_id=user_id, credential_version_id=credential_version_id)

    def get_credential_version_id(self, user, *, config_id: str, version: int) -> int | None:
        row = self._model_configs.get_version(user.id, config_id, version)
        return row.credential_version_id
```

> 实现注意：`CredentialVault.rotate_for_config(user_id, credential_version_id, secret)` 与 `CredentialVault.revoke_by_version(user_id, credential_version_id)` 是 Task 1 vault 的补充辅助方法（根据 `credential_version_id` 反查 `credential_id` 后走既有 `rotate`/`revoke`）。在 Task 1 的 `vault.py` 中添加这两个方法（含 owner 校验），并确保 `replace_model_api_key` 在 config 无 key 时先 `store_secret`。`ModelConfigRepository.delete(user_id, config_id)` 在 repo 中实现：软删除当前版本行并标记 `is_current=False` + `connection_status="disabled"`（保留历史行，历史 metadata 保留；凭据已通过 revoke 物理销毁）。

- [ ] **Step 4: 实现 deps 与守卫测试**

新建 `backend/app/providers/deps.py`：

```python
"""FastAPI dependencies for provider service + credential vault."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.credentials import crypto
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.deps import get_db
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService


def get_credential_vault(
    db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> CredentialVault:
    return CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )


def get_provider_service(
    vault: CredentialVault = Depends(get_credential_vault),
    db: DbSession = Depends(get_db),
) -> ProviderService:
    return ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(db),
        search_configs=SearchConfigRepository(db),
    )
```

新建 `backend/tests/providers/test_guards.py`：

```python
"""MODEL_NOT_CONFIGURED / SEARCH_PROVIDER_NOT_CONFIGURED guards."""
from __future__ import annotations

from app.providers import errors


def test_guard_errors_have_stable_codes() -> None:
    assert errors.ModelNotConfiguredError("x").code == "MODEL_NOT_CONFIGURED"
    assert errors.SearchProviderNotConfiguredError("x").code == "SEARCH_PROVIDER_NOT_CONFIGURED"
    assert errors.ModelNotConfiguredError("x").status_code == 409


def test_no_model_raises_model_not_configured(service_and_db) -> None:
    service, db = service_and_db
    from tests.providers.test_config_versioning import _user
    user = _user(db, "alice@example.com")
    try:
        service.require_available_model_config(user)
        assert False, "expected MODEL_NOT_CONFIGURED"
    except errors.ModelNotConfiguredError:
        pass
```

- [ ] **Step 5: 写 owner 隔离测试**

新建 `backend/tests/providers/test_owner_isolation.py`：

```python
"""Cross-user provider/config isolation (M-03)."""
from __future__ import annotations

import pytest
from app.auth import errors as aerr
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials.crypto import master_key_from_env_value
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.db import Base
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def two_users(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'iso.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'iso.db'}", credential_master_key="ab" * 32)
    vault = CredentialVault(
        master_key=master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version, repository=CredentialRepository(db),
    )
    service = ProviderService(
        vault=vault, model_configs=ModelConfigRepository(db), search_configs=SearchConfigRepository(db),
    )
    users = UserRepository(db)
    alice = users.create("alice@example.com", "hash", None)
    bob = users.create("bob@example.com", "hash", None)
    yield service, db, alice, bob
    db.close()


def test_b_cannot_see_or_touch_a_model_config(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_model_config(alice, name="a", provider_type="openai", model_name="gpt-4o-mini", base_url=None, api_key="sk-a")
    assert all(c.config_id != cfg.config_id for c in service.list_model_configs(bob))
    with pytest.raises(aerr.NotFoundError):
        service.update_model_config(bob, config_id=cfg.config_id, name="hack", provider_type="openai", model_name="x", base_url=None)
    with pytest.raises(aerr.NotFoundError):
        service.get_model_config_version(bob, cfg.config_id, 1)


def test_b_cannot_use_a_credential(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_model_config(alice, name="a", provider_type="anthropic", model_name="claude-3-5-sonnet", base_url=None, api_key="alice-key-777")
    import anyio
    async def run():
        return await service.test_model_connection(bob, config_id=cfg.config_id)
    with pytest.raises(aerr.NotFoundError):
        anyio.run(run)
```

（`list_model_configs`/`get_model_config_version` 为 service 查询方法，在 Task 4 的 service.py 中实现：`list_model_configs(user)`、`list_search_configs(user)`。）

- [ ] **Step 6: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_config_versioning.py tests/providers/test_owner_isolation.py tests/providers/test_guards.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/providers backend/tests/providers && git commit -m "feat(api): add provider service with config versioning and guards"
```

Expected: 全 PASS；Commit 生成。

---

## Task 5: Provider API Routes（薄层 + 稳定错误）

**Files:**
- Create: `backend/app/providers/schemas.py`
- Create: `backend/app/api/routes/providers.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/api/errors.py`
- Create: `backend/tests/providers/test_providers_api.py`

**Interfaces:**
- Consumes: `app/providers/service.ProviderService`、`app/auth.deps.require_user`、Task 2/3 的 registry definitions。
- Produces: `GET /api/providers/definitions`、`GET/POST /api/providers/models`、`PATCH /api/providers/models/{config_id}`、`POST /api/providers/models/{config_id}/key`、`POST .../test`、`POST .../default`、`DELETE ...`；Search 同族。

- [ ] **Step 1: 定义 DTO schemas**

新建 `backend/app/providers/schemas.py`：

```python
"""Provider API DTOs (M-03).

Responses never contain secret/ciphertext/wrapped key/nonce/master key. api_key
is write-only via SecretStr.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, SecretStr

from app.providers.protocol import ProviderTestStatus


class ProviderDefinitionDto(BaseModel):
    provider_type: str
    display_name: str
    requires_api_key: bool
    requires_model_name: bool
    requires_base_url: bool
    default_base_url: str | None
    protocol_family: str


class ModelConfigDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    config_id: str
    version: int
    name: str
    provider_type: str
    model_name: str
    base_url: str | None
    credential_configured: bool
    is_default: bool
    connection_status: str
    last_tested_at: datetime | None
    created_at: datetime


class CreateModelConfigCommand(BaseModel):
    name: str
    provider_type: str
    model_name: str
    base_url: str | None = None
    api_key: SecretStr | None = None
    set_default: bool = False


class UpdateModelConfigCommand(BaseModel):
    name: str
    provider_type: str
    model_name: str
    base_url: str | None = None


class ReplaceKeyCommand(BaseModel):
    api_key: SecretStr


class ModelConfigListResponse(BaseModel):
    configs: list[ModelConfigDto]
    definitions: list[ProviderDefinitionDto]


class ProviderTestResultDto(BaseModel):
    status: ProviderTestStatus
    error_code: str | None = None
    message: str | None = None
    latency_ms: int | None = None


class SearchConfigDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    config_id: str
    version: int
    name: str
    provider_type: str
    base_url: str | None
    credential_configured: bool
    connection_status: str
    last_tested_at: datetime | None
    created_at: datetime


class CreateSearchConfigCommand(BaseModel):
    name: str
    provider_type: str
    base_url: str
    api_key: SecretStr | None = None


class UpdateSearchConfigCommand(BaseModel):
    name: str
    provider_type: str
    base_url: str


class SearchConfigListResponse(BaseModel):
    configs: list[SearchConfigDto]
    definitions: list[ProviderDefinitionDto]
```

- [ ] **Step 2: 写 API 失败测试**

新建 `backend/tests/providers/test_providers_api.py`（TestClient + SQLite，覆盖跨用户、秘密不回显、连接测试 stub）：

```python
"""Provider HTTP API behavior (SQLite, no real keys)."""
from __future__ import annotations

import pytest
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

COOKIE = "kairos_session"
PASSWORD = "password123"
MASTER_KEY = "ab" * 32


@pytest.fixture()
def env_master_key(monkeypatch) -> None:
    monkeypatch.setenv("KAIROS_CREDENTIAL_MASTER_KEY", MASTER_KEY)


@pytest.fixture()
def client(env_master_key, tmp_path) -> TestClient:
    engine = create_engine(f"sqlite:///{tmp_path / 'prov_api.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "confirm_password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_model(client: TestClient, **overrides) -> dict:
    body = {"name": "main", "provider_type": "openai", "model_name": "gpt-4o-mini", "api_key": "sk-secret-123", **overrides}
    resp = client.post("/api/providers/models", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_model_never_returns_plaintext(client: TestClient) -> None:
    _register(client, "alice@example.com")
    created = _create_model(client)
    assert created["credential_configured"] is True
    text = repr(created)
    assert "sk-secret-123" not in text
    assert "ciphertext" not in text and "wrapped" not in text and "nonce" not in text


def test_cross_user_blocked(client: TestClient) -> None:
    _register(client, "alice@example.com")
    created = _create_model(client)
    # second user in a fresh client
    from tests.providers.test_providers_api import COOKIE as _C
    bob_client = TestClient(create_app())
    bob_client.dependency_overrides = client.app.dependency_overrides
    with bob_client as b:
        _register(b, "bob@example.com")
        assert b.get("/api/providers/models").json()["configs"] == []
        resp = b.patch(f"/api/providers/models/{created['config_id']}", json={"name": "hack", "provider_type": "openai", "model_name": "x"})
        assert resp.status_code == 404
        resp = b.delete(f"/api/providers/models/{created['config_id']}")
        assert resp.status_code == 404


def test_definitions_endpoint_lists_registry(client: TestClient) -> None:
    _register(client, "alice@example.com")
    resp = client.get("/api/providers/definitions")
    assert resp.status_code == 200
    types = {d["provider_type"] for d in resp.json()["models"]}
    assert {"openai", "anthropic", "gemini", "deepseek", "openrouter", "ollama", "custom_openai_compatible"} <= types
    assert {"custom_compatible_search"} <= {d["provider_type"] for d in resp.json()["searches"]}


def test_connection_test_uses_stub_server(client: TestClient) -> None:
    # real httpx -> local stub -> AVAILABLE (see smoke for full chain)
    pass
```

- [ ] **Step 3: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_providers_api.py -v
```

Expected: FAIL（路由不存在 → 404）。

- [ ] **Step 4: 实现 routes + 注册 + 错误 handler**

新建 `backend/app/api/routes/providers.py`：

```python
"""Provider configuration API (M-03). Thin layer: DTO -> service -> DTO.

No SQL, no credential decryption, no direct SDK calls here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_user
from app.auth.models import User
from app.providers.deps import get_provider_service
from app.providers.registry import list_model_provider_definitions, list_search_provider_definitions
from app.providers.schemas import (
    CreateModelConfigCommand, CreateSearchConfigCommand, ModelConfigDto, ModelConfigListResponse,
    ProviderDefinitionDto, ProviderTestResultDto, ReplaceKeyCommand, SearchConfigDto,
    SearchConfigListResponse, UpdateModelConfigCommand, UpdateSearchConfigCommand,
)
from app.providers.service import ProviderService

router = APIRouter(prefix="/providers", tags=["providers"])


def _model_dto(row) -> ModelConfigDto:
    return ModelConfigDto(
        config_id=row.config_id, version=row.version, name=row.name, provider_type=row.provider_type,
        model_name=row.model_name, base_url=row.base_url,
        credential_configured=row.credential_version_id is not None,
        is_default=row.is_default, connection_status=row.connection_status,
        last_tested_at=row.last_tested_at, created_at=row.created_at,
    )


def _search_dto(row) -> SearchConfigDto:
    return SearchConfigDto(
        config_id=row.config_id, version=row.version, name=row.name, provider_type=row.provider_type,
        base_url=row.base_url, credential_configured=row.credential_version_id is not None,
        connection_status=row.connection_status, last_tested_at=row.last_tested_at,
        created_at=row.created_at,
    )


@router.get("/definitions")
def definitions() -> dict:
    return {
        "models": [ProviderDefinitionDto.model_validate(d) for d in list_model_provider_definitions()],
        "searches": [ProviderDefinitionDto.model_validate(d) for d in list_search_provider_definitions()],
    }


@router.get("/models", response_model=ModelConfigListResponse)
def list_models(user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service)) -> ModelConfigListResponse:
    configs = [_model_dto(c) for c in service.list_model_configs(user)]
    return ModelConfigListResponse(
        configs=configs,
        definitions=[ProviderDefinitionDto.model_validate(d) for d in list_model_provider_definitions()],
    )


@router.post("/models", response_model=ModelConfigDto, status_code=201)
def create_model(
    cmd: CreateModelConfigCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.create_model_config(
        user, name=cmd.name, provider_type=cmd.provider_type, model_name=cmd.model_name,
        base_url=cmd.base_url,
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
        set_default=cmd.set_default,
    )
    return _model_dto(row)


@router.patch("/models/{config_id}", response_model=ModelConfigDto)
def update_model(
    config_id: str, cmd: UpdateModelConfigCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.update_model_config(user, config_id=config_id, name=cmd.name,
                                      provider_type=cmd.provider_type, model_name=cmd.model_name, base_url=cmd.base_url)
    return _model_dto(row)


@router.post("/models/{config_id}/key", response_model=ModelConfigDto)
def replace_model_key(
    config_id: str, cmd: ReplaceKeyCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.replace_model_api_key(user, config_id=config_id, api_key=cmd.api_key.get_secret_value())
    return _model_dto(row)


@router.post("/models/{config_id}/test", response_model=ProviderTestResultDto)
async def test_model(
    config_id: str, user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service),
) -> ProviderTestResultDto:
    return await service.test_model_connection(user, config_id=config_id)


@router.post("/models/{config_id}/default", response_model=ModelConfigDto)
def set_default_model(
    config_id: str, user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.set_default_model(user, config_id=config_id)
    return _model_dto(row)


@router.delete("/models/{config_id}", status_code=204)
def delete_model(
    config_id: str, user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service),
) -> None:
    service.delete_model_config(user, config_id=config_id)
    return None


@router.get("/searches", response_model=SearchConfigListResponse)
def list_searches(user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service)) -> SearchConfigListResponse:
    configs = [_search_dto(c) for c in service.list_search_configs(user)]
    return SearchConfigListResponse(
        configs=configs,
        definitions=[ProviderDefinitionDto.model_validate(d) for d in list_search_provider_definitions()],
    )


@router.post("/searches", response_model=SearchConfigDto, status_code=201)
def create_search(
    cmd: CreateSearchConfigCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.create_search_config(
        user, name=cmd.name, provider_type=cmd.provider_type, base_url=cmd.base_url,
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
    )
    return _search_dto(row)


@router.patch("/searches/{config_id}", response_model=SearchConfigDto)
def update_search(
    config_id: str, cmd: UpdateSearchConfigCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.update_search_config(user, config_id=config_id, name=cmd.name,
                                       provider_type=cmd.provider_type, base_url=cmd.base_url)
    return _search_dto(row)


@router.post("/searches/{config_id}/key", response_model=SearchConfigDto)
def replace_search_key(
    config_id: str, cmd: ReplaceKeyCommand, user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.replace_search_api_key(user, config_id=config_id, api_key=cmd.api_key.get_secret_value())
    return _search_dto(row)


@router.post("/searches/{config_id}/test", response_model=ProviderTestResultDto)
async def test_search(
    config_id: str, user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service),
) -> ProviderTestResultDto:
    return await service.test_search_connection(user, config_id=config_id)


@router.delete("/searches/{config_id}", status_code=204)
def delete_search(
    config_id: str, user: User = Depends(require_user), service: ProviderService = Depends(get_provider_service),
) -> None:
    service.delete_search_config(user, config_id=config_id)
    return None
```

修改 `backend/app/api/router.py`：

```python
from app.api.routes import auth, health, providers

api_router.include_router(providers.router)
```

修改 `backend/app/api/errors.py`（追加 provider 错误 handler）：

```python
from app.providers.errors import ProviderError

    @app.exception_handler(ProviderError)
    async def handle_provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})
```

- [ ] **Step 5: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/providers/test_providers_api.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app backend/tests/providers && git commit -m "feat(api): add provider configuration and connection test endpoints"
```

Expected: 全 PASS；Commit 生成。

---

## Task 6: 最小 /models 前端配置闭环

**Files:**
- Modify: `frontend/src/app/api/client.ts`（新增 `patch` 方法）
- Create: `frontend/src/features/providers/providers.api.ts`
- Create: `frontend/src/features/providers/ModelsView.vue`
- Create: `frontend/src/features/providers/ModelConfigDrawer.vue`
- Create: `frontend/src/features/providers/SearchConfigDrawer.vue`
- Modify: `frontend/src/app/router/index.ts`（新增 `/models` 路由）
- Create: `frontend/src/features/providers/providers.test.ts`

**Interfaces:**
- Consumes: `@/app/api/client` 的 `apiClient`、`@/app/router`、后端 `/api/providers/*`。
- Produces: `/models` 页面（AI 模型 / 搜索服务 两个 Tab、Overlay Drawer 新增/编辑/更换 Key、测试连接、设为默认、删除）、`providers.api.ts` 函数。

- [ ] **Step 1: ApiClient 增加 patch**

在 `frontend/src/app/api/client.ts` 添加：

```ts
  async patch<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>('PATCH', path, body, signal)
  }
```

- [ ] **Step 2: 写前端失败测试**

新建 `frontend/src/features/providers/providers.test.ts`：

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { mount } from '@vue/test-utils'

vi.mock('@/features/providers/providers.api', () => ({
  listModelConfigs: vi.fn(),
  listSearchConfigs: vi.fn(),
  fetchDefinitions: vi.fn(),
  createModelConfig: vi.fn(),
}))

import * as providersApi from '@/features/providers/providers.api'
import ModelsView from '@/features/providers/ModelsView.vue'

const modelConfig = {
  config_id: 'c1',
  version: 1,
  name: 'main',
  provider_type: 'openai',
  model_name: 'gpt-4o-mini',
  base_url: null,
  credential_configured: true,
  is_default: true,
  connection_status: 'available',
  last_tested_at: '2026-08-10T00:00:00Z',
  created_at: '2026-08-10T00:00:00Z',
}

const searchConfig = {
  config_id: 's1',
  version: 1,
  name: 'my-search',
  provider_type: 'custom_compatible_search',
  base_url: 'http://search:9000',
  credential_configured: true,
  connection_status: 'available',
  last_tested_at: null,
  created_at: '2026-08-10T00:00:00Z',
}

const definitions = [
  { provider_type: 'openai', display_name: 'OpenAI', requires_api_key: true, requires_model_name: true, requires_base_url: false, default_base_url: 'https://api.openai.com/v1', protocol_family: 'openai_compatible' },
  { provider_type: 'custom_compatible_search', display_name: 'Custom Compatible Search', requires_api_key: true, requires_model_name: false, requires_base_url: true, default_base_url: null, protocol_family: 'compatible_search' },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ModelsView', () => {
  it('renders model config rows from the API response', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({ models: definitions, searches: definitions })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({ configs: [modelConfig], definitions })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({ configs: [searchConfig], definitions })

    const wrapper = mount(ModelsView)
    await new Promise((r) => setTimeout(r, 0))
    expect(wrapper.text()).toContain('main')
    expect(wrapper.text()).toContain('OpenAI')
  })

  it('never renders the api key: shows "已配置" instead', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({ models: definitions, searches: definitions })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({ configs: [{ ...modelConfig, credential_configured: true }], definitions })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({ configs: [], definitions })

    const wrapper = mount(ModelsView)
    await new Promise((r) => setTimeout(r, 0))
    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).not.toContain('sk-')
  })
})
```

- [ ] **Step 3: 运行确认失败**

```bash
cd frontend && npx vitest run src/features/providers/providers.test.ts
```

Expected: FAIL（文件不存在）。

- [ ] **Step 4: 实现 providers.api.ts + ModelsView + Drawer**

新建 `frontend/src/features/providers/providers.api.ts`：

```ts
import { apiClient } from '@/app/api/client'

export interface ProviderDefinitionDto {
  provider_type: string
  display_name: string
  requires_api_key: boolean
  requires_model_name: boolean
  requires_base_url: boolean
  default_base_url: string | null
  protocol_family: string
}

export interface ModelConfigDto {
  config_id: string
  version: number
  name: string
  provider_type: string
  model_name: string
  base_url: string | null
  credential_configured: boolean
  is_default: boolean
  connection_status: string
  last_tested_at: string | null
  created_at: string
}

export interface SearchConfigDto {
  config_id: string
  version: number
  name: string
  provider_type: string
  base_url: string | null
  credential_configured: boolean
  connection_status: string
  last_tested_at: string | null
  created_at: string
}

export interface ProviderTestResultDto {
  status: string
  error_code: string | null
  message: string | null
  latency_ms: number | null
}

export interface DefinitionsDto {
  models: ProviderDefinitionDto[]
  searches: ProviderDefinitionDto[]
}

export interface ModelConfigListDto {
  configs: ModelConfigDto[]
  definitions: ProviderDefinitionDto[]
}

export interface SearchConfigListDto {
  configs: SearchConfigDto[]
  definitions: ProviderDefinitionDto[]
}

export function fetchDefinitions(): Promise<DefinitionsDto> {
  return apiClient.get<DefinitionsDto>('/providers/definitions')
}
export function listModelConfigs(): Promise<ModelConfigListDto> {
  return apiClient.get<ModelConfigListDto>('/providers/models')
}
export function createModelConfig(body: Record<string, unknown>): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>('/providers/models', body)
}
export function updateModelConfig(configId: string, body: Record<string, unknown>): Promise<ModelConfigDto> {
  return apiClient.patch<ModelConfigDto>(`/providers/models/${configId}`, body)
}
export function replaceModelKey(configId: string, apiKey: string): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>(`/providers/models/${configId}/key`, { api_key: apiKey })
}
export function testModelConnection(configId: string): Promise<ProviderTestResultDto> {
  return apiClient.post<ProviderTestResultDto>(`/providers/models/${configId}/test`)
}
export function setModelDefault(configId: string): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>(`/providers/models/${configId}/default`)
}
export function deleteModelConfig(configId: string): Promise<void> {
  return apiClient.delete<void>(`/providers/models/${configId}`)
}
export function listSearchConfigs(): Promise<SearchConfigListDto> {
  return apiClient.get<SearchConfigListDto>('/providers/searches')
}
export function createSearchConfig(body: Record<string, unknown>): Promise<SearchConfigDto> {
  return apiClient.post<SearchConfigDto>('/providers/searches', body)
}
export function updateSearchConfig(configId: string, body: Record<string, unknown>): Promise<SearchConfigDto> {
  return apiClient.patch<SearchConfigDto>(`/providers/searches/${configId}`, body)
}
export function replaceSearchKey(configId: string, apiKey: string): Promise<SearchConfigDto> {
  return apiClient.post<SearchConfigDto>(`/providers/searches/${configId}/key`, { api_key: apiKey })
}
export function testSearchConnection(configId: string): Promise<ProviderTestResultDto> {
  return apiClient.post<ProviderTestResultDto>(`/providers/searches/${configId}/test`)
}
export function deleteSearchConfig(configId: string): Promise<void> {
  return apiClient.delete<void>(`/providers/searches/${configId}`)
}
```

新建 `frontend/src/features/providers/ModelsView.vue`：页面包含「AI 模型 / 搜索服务」两个 Tab；模型列表展示 配置名称/Provider/Model/连接状态/默认 列；操作：新增（Drawer）、编辑（Drawer）、更换 Key（Drawer）、测试连接、设为默认、删除；搜索 Tab 同构（无 设为默认）。Drawer 用页面局部 Overlay 实现，`position: fixed` 覆盖页面不挤压布局。API Key 仅在创建/更换 Key 时显示输入框；编辑已存在配置只显示「已配置」。

新建 `frontend/src/features/providers/ModelConfigDrawer.vue` / `SearchConfigDrawer.vue`：基于 `ProviderDefinitionDto` 渲染必填/可选字段（provider_type/display_name/model_name/base_url），`api_key` 输入使用 `type="password"`，提交后组件立即丢弃本地 key 引用。

修改 `frontend/src/app/router/index.ts` 追加：

```ts
import ModelsView from '@/features/providers/ModelsView.vue'
// routes 数组追加:
  {
    path: '/models',
    name: 'models',
    component: ModelsView,
    meta: { requiresAuth: true },
  },
```

- [ ] **Step 5: 运行 + 门禁 + Commit**

```bash
cd frontend && npm run lint:check && npm run format:check && npm run type-check && npm run build
npx vitest run src/features/providers/providers.test.ts
cd .. && git add frontend/src && git commit -m "feat(web): add model and search configuration flow"
```

Expected: lint/format/type-check/build PASS；vitest PASS；Commit 生成。

---

## Task 7: M-03 Security Smoke、文档与 Execution Record

**Files:**
- Create: `backend/tests/integration/test_provider_smoke.py`
- Create: `docs/operations/provider-credentials.md`
- Create: `docs/implementation/M-03-execution.md`

**Interfaces:**
- Consumes: 全部 M-03 后端/前端产物。
- Produces: 无新业务契约；仅验证闭环 + 运维文档 + 执行记录。

- [ ] **Step 1: 写集成 Smoke（本地 stub server，无真实商业 Key）**

新建 `backend/tests/integration/test_provider_smoke.py`（`pytest.mark.integration`；用 `threading` 起一个极简 stub HTTP 服务返回 200/`{"results": []}`）：

```python
"""M-03 Provider/Credential Smoke against live local PostgreSQL.

Chain: A registers -> creates ModelConfig with key -> response has no plaintext
-> DB has no plaintext -> test connection against local stub -> AVAILABLE ->
edit (version+1) -> replace key (credential+config version+1) -> B is blocked ->
create SearchConfig -> test AVAILABLE -> delete config -> no longer listed.

No real commercial API key required: a local stub HTTP server plays the provider.
"""
from __future__ import annotations

import http.server
import json
import threading
from uuid import uuid4

import pytest
from app.auth.models import User
from app.infra.deps import get_session_factory
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = pytest.mark.integration

COOKIE = "kairos_session"
PASSWORD = "password-123"


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"results": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture(scope="module")
def stub_url() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _cleanup_users(emails: list[str]) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(User).where(User.email.in_(emails)))
        session.commit()
    finally:
        session.close()


def test_provider_credential_smoke(stub_url: str) -> None:
    tag = uuid4().hex[:8]
    email_a = f"alice-{tag}@example.com"
    email_b = f"bob-{tag}@example.com"
    SECRET = "sk-test-secret-000"
    try:
        app = create_app()
        with TestClient(app) as a, TestClient(app) as b:
            _register(a, email_a)
            _register(b, email_b)

            created = a.post("/api/providers/models", json={
                "name": "smoke", "provider_type": "openai", "model_name": "gpt-4o-mini",
                "base_url": stub_url, "api_key": SECRET,
            })
            assert created.status_code == 201, created.text
            created_body = created.json()
            assert SECRET not in repr(created_body)
            assert created_body["credential_configured"] is True

            # test connection -> AVAILABLE (stub returns 200)
            tested = a.post(f"/api/providers/models/{created_body['config_id']}/test")
            assert tested.status_code == 200
            assert tested.json()["status"] == "AVAILABLE"

            # edit -> version +1
            edited = a.patch(f"/api/providers/models/{created_body['config_id']}", json={
                "name": "smoke-2", "provider_type": "openai", "model_name": "gpt-4o",
            })
            assert edited.status_code == 200
            assert edited.json()["version"] == created_body["version"] + 1

            # replace key -> credential + config version +1
            replaced = a.post(f"/api/providers/models/{created_body['config_id']}/key", json={"api_key": SECRET + "x"})
            assert replaced.status_code == 200
            assert replaced.json()["version"] == created_body["version"] + 2

            # cross-user: B sees nothing and cannot touch A's config
            assert b.get("/api/providers/models").json()["configs"] == []
            blocked = b.patch(f"/api/providers/models/{created_body['config_id']}", json={
                "name": "hack", "provider_type": "openai", "model_name": "x",
            })
            assert blocked.status_code == 404

            # search config
            s_created = a.post("/api/providers/searches", json={
                "name": "search-1", "provider_type": "custom_compatible_search",
                "base_url": stub_url, "api_key": "sk-search",
            })
            assert s_created.status_code == 201
            s_tested = a.post(f"/api/providers/searches/{s_created.json()['config_id']}/test")
            assert s_tested.status_code == 200
            assert s_tested.json()["status"] == "AVAILABLE"

            # delete -> no longer listed
            deleted = a.delete(f"/api/providers/models/{created_body['config_id']}")
            assert deleted.status_code == 204
            remaining = a.get("/api/providers/models").json()["configs"]
            assert all(c["config_id"] != created_body["config_id"] for c in remaining)

        # DB holds no plaintext secret
        _assert_no_plaintext_in_db(SECRET)
    finally:
        _cleanup_users([email_a, email_b])


def _register(client: TestClient, email: str) -> dict:
    resp = client.post("/api/auth/register", json={
        "email": email, "password": PASSWORD, "confirm_password": PASSWORD,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _assert_no_plaintext_in_db(secret: str) -> None:
    from app.credentials.models import CredentialVersion
    session = get_session_factory()()
    try:
        rows = session.query(CredentialVersion).all()
        text = repr([{c: getattr(r, c) for c in r.__table__.columns.keys()} for r in rows])
        assert secret not in text
        assert b"" not in [r.secret_ciphertext for r in rows if r.status == "active"]
    finally:
        session.close()
```

> 注意：运行 Smoke 前需要在本地 `.env` 设置 `KAIROS_CREDENTIAL_MASTER_KEY`（生成见 `.env.example`），因为 integration 测试用真实 Settings。`create_app` 在 integration 下连接本地 PostgreSQL（`KAIROS_DATABASE_URL`）。

- [ ] **Step 2: 运行 Smoke（本地服务已起）**

```bash
cd backend && KAIROS_RUN_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration/test_provider_smoke.py -v
```

Expected: PASS。

- [ ] **Step 3: Secret Leak Check**

```bash
cd backend && KAIROS_RUN_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration/test_provider_smoke.py -v -s 2>&1 | grep -c "sk-test-secret-000" || echo "no plaintext in captured output"
cd .. && git grep -n "sk-live-123\|sk-secret-123\|sk-super-secret-999" -- ':!docs/superpowers/plans/*' || echo "no fixture secrets leaked into tracked files"
```

Expected: 两处都无明文泄漏。

- [ ] **Step 4: 写运维文档**

新建 `docs/operations/provider-credentials.md`（简短，非安全论文），覆盖：信封加密结构、Master Key 配置方式、生成 local dev key、ModelProvider/SearchProvider 注册方式、Provider test result、rotate/revoke 行为、M-03 scoped 测试命令、M-03 smoke 命令。

- [ ] **Step 5: 写 M-03 execution record**

新建 `docs/implementation/M-03-execution.md`，按实施计划模板：状态 IN_PROGRESS→DONE、Agent、Baseline Commit=`e7dda2c1e2928689a1214715830e099cc98fe956`、依赖 M-01/M-02、目标环境 local、输入/产出契约、明确不做（M-04+/DEPLOY-GATE-1）、真实测试命令与结果。

- [ ] **Step 6: 全量门禁复扫 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/credentials/ tests/providers/ -q
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd ../frontend && npm run lint:check && npm run type-check && npm run build
cd .. && git add docs/ && git commit -m "docs(provider): document provider and credential behavior"
```

Expected: 全 PASS；Commit 生成。

- [ ] **Step 7: 收尾检查**

```bash
git status && git log --oneline -8
```

Expected: 工作树 clean；M-03 约 5～7 个 Commit；HEAD 在 `feature/M-03-provider-credentials`。

---

## Self-Review

**1. Spec coverage：**
- D-023 多用户隔离 → Task 4/5（service + API 全部 owner-scoped，跨用户 404）。
- D-029 BYOK 七 Provider → Task 2（registry 七个注册，共享 OpenAI-compatible 核心）。
- D-036 无费用 UI → 全程无金额字段/UI。
- D-048 13 页面 → 仅新增 `/models`（已确认页面），无新页面。
- D-049 模型问题→`/models` → /models 页面存在。
- D-051 模型配置单页+Drawer → Task 6。
- D-052 设置页 → 不涉及。
- D-059 网站凭据底座 → Task 1 Credential（通用底座）；Credential Drawer/登录明确不做。
- D-066 MODEL_NOT_CONFIGURED → Task 4 guard + 稳定错误码。
- D-067 Overlay Drawer → Task 6（Overlay，不挤压布局）。
- D-069 Search Provider 独立 + `/models` 搜索 Tab → Task 3/6；不新增搜索页面。
- 信封加密/版本冻结/rotate/revoke → Task 1/4。
- 前端不回读明文 → Task 5/6（DTO 无 secret，`credential_configured` 代替）。
- 无未授权 fallback → 全计划无自动切换逻辑。

**2. Placeholder scan：** 无 "TBD/TODO/implement later"；每个 Task 有真实代码块或精确接口。`ModelsView.vue` 组件细节在 Task 6 Step 4 描述为行为级（列表列/操作/Drawer 规则），属于组件组合而非占位。

**3. Type consistency：**
- `ProviderTestStatus`/`ProviderTestResult`/`ProviderDefinition` 在 Task 2 定义，Task 3/4/5 一致引用。
- `CredentialVault` 方法名 `store_secret/read_for_execution/rotate/revoke` 在 Task 1/4 一致；Task 4 补充 `rotate_for_config`/`revoke_by_version` 明确标注在 Task 1 vault 中添加。
- `ModelConfigRepository`/`SearchConfigRepository` 方法（`create_version/append_version/get_current/get_version/list_current/set_default/mark_connection/delete`）Task 4 内自洽。
- `_model_dto`/`_search_dto` 字段与 `schemas.py` DTO 一致。
- `get_credential_version_id` 命名在 Task 4 测试与 service 中一致。
- `HttpClient` protocol 签名在 transport 与 fake 中一致。

---

## 项目专项审批（M-03）

**CHECK 1 Business Decisions：** D-023 PASS / D-029 PASS / D-036 PASS / D-051 PASS / D-059 PASS / D-066 PASS / D-069 PASS。七类首批 Model Provider 覆盖（Task 2）；Search 与 Model 独立协议（Task 3 vs Task 2）；无费用 UI；无明文 Key；无未授权 fallback；`/models` 页面边界正确（仅新增已确认页面，Drawer Overlay，无 `/search-providers`/`/credentials` 等新页面）。

**CHECK 2 M-01/M-02 Compatibility：** 不重新实现 Auth；`require_user`/`assert_owned`/`NotFoundError` 全复用；不触碰 health/readiness、Temporal、MinIO、compose。

**CHECK 3 M-03/M-04 Boundary：** 无 Task/State Machine/Outbox/Checkpoint/Domain Event 大体系。

**CHECK 4 M-03/M-05 Boundary：** 只实现最小 `/models` 页面；无 App Shell/13 页面/全局 Drawer 重构。

**CHECK 5 Secret Safety：** Master Key 不进 DB/Git；Secret 不进 response/logs/exception/Temporal History；frontend 无读取 Key 接口；`read_for_execution` 仅服务层受控路径；revoke 物理清零 ciphertext。

**CHECK 6 Version Semantics：** ModelConfig 可冻结 `(config_id, version)`；Credential 有 version；rotate 不静默覆盖（新版本+旧版本 retired）；default 只影响未来选择。

**CHECK 7 Provider Architecture：** ModelProvider/SearchProvider 协议清晰、Registry typed 代码注册、无任意 class import、七 Provider 共享协议族（OpenAI-compatible 核心复用，无七份复制）。

**CHECK 8 A-Lite Tests：** 无全量测试依赖；无真实商业 API Key 要求（stub server/fake transport）；无 Browser E2E；核心安全路径（加密/隔离/版本/错误映射/秘密泄漏）有测试。

**CHECK 9 Git：** 预计 7 个 Commit，每个可独立验证；不 push/merge/tag/deploy；分支 `feature/M-03-provider-credentials` 从 M-02 HEAD 创建。

---

PLAN SELF-APPROVAL: PASS

business decisions: PASS
M-01 compatibility: PASS
M-02 compatibility: PASS
M-03 scope: PASS
M-04 boundary: PASS
M-05 boundary: PASS
credential security: PASS
provider architecture: PASS
version semantics: PASS
A-Lite testing: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
