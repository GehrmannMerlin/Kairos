# M-04 核心领域数据模型、状态机、事件、幂等与 Checkpoint 基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 D-004～D-008、D-011、D-015、D-016、D-030 的核心业务事实固化为 PostgreSQL 领域模型与领域服务，建立 Task/Node 状态机、事务性 DomainEvent+Outbox、幂等身份、Checkpoint 与乐观锁/owner 基础，为 M-05 起所有 UI 与 M-07 Temporal 提供唯一可信状态模型。

**Architecture:** 在 `app/domain/`（ORM 模型、Repository、Service、幂等、Checkpoint、错误）与 `app/state/`（状态机枚举/转换矩阵/allowed_actions、事件/Outbox 追加）两个新包中实现。所有核心表显式 `user_id NOT NULL` 并复用 M-02 `errors.assert_owned`；状态变化一律经 `transition_task`/`transition_node`，在**同一 DB 事务**内完成「校验转换 → 更新 current state + version → append DomainEvent → enqueue Outbox → commit」，任一失败整体 rollback。Checkpoint 只在批次业务事务 COMMIT 成功后单独创建；Temporal heartbeat 不冒充 Checkpoint。

**Tech Stack:** SQLAlchemy 2（复用 M-01 `app.infra.db.Base`/`get_db`）、Alembic（0004）、Python 3.11 + typing、`hashlib.sha256` + canonical JSON fingerprint。无新第三方运行时依赖。

## Global Constraints

- 复用 M-02 `assert_owned` / `NotFoundError`；所有 Repository 读取必须 owner-scoped，禁止默认全表读取。**不实现第二套 auth/ownership。**
- 不触碰/不重写 M-01（DatabaseSession/Temporal/MinIO/health/OTel/compose）、M-02（User/Session/Auth）、M-03（Credential/Provider）。
- 状态变化只能经 Command → State Machine → 校验 → 同事务写 state + event + outbox。禁止散落 `if state == ...`。
- 所有状态 enum 名全项目唯一（canonical vocabulary）；不得同时出现同义多个名称。
- DomainEvent append-only，禁止 UPDATE 历史事件；payload 禁止存 API Key/Cookie/密码/Authorization。
- 幂等键 = stable fingerprint（canonical JSON + SHA-256）+ DB 唯一约束兜底；禁止 random-only 幂等。
- Checkpoint 只代表已 COMMIT 的业务进度；heartbeat 不冒充。
- 软删除：Task `DELETED` + `deleted_at`；运行中状态不得直接 delete；永久级联清理留给 M-15。
- Migration 0004 可逆（downgrade 可回 0003）；所有用户业务表 `user_id NOT NULL`。
- 只跑 M-04 scoped tests；禁止默认 `pytest tests/`、禁真实 Provider live、禁 Browser E2E、禁压力测试。
- 不 push / 不 merge / 不 tag / 不 deploy；本地 5～7 个 Commit。分支 `feature/M-04-domain-state-idempotency`，基线 SHA = M-03 HEAD（`602a5c30a8270de27206063ac2e1c2ea5efd7002`）。
- DEPLOY-GATE-1：M-04 完成后做 Preflight；当前仓库无 CI/remote/Registry/服务器/域名/SSH，预计 `BLOCKED_EXTERNAL`。

---

## 术语与全局共享接口（跨 Task 一致）

**Canonical TaskState**（存 DB 用 `.value`，大写）：
`DRAFT, QUEUED, RUNNING, PAUSING, PAUSED, WAITING_APPROVAL, WAITING_RESOURCE, CANCELLING, CANCELLED, COMPLETED, PARTIALLY_COMPLETED, FAILED, DELETED`

**Canonical NodeState**：
`PENDING, READY, RUNNING, WAITING_RETRY, WAITING_RESOURCE, SUCCEEDED, SKIPPED, BLOCKED, FAILED, CANCELLED`

**命令 action 名称**（`allowed_actions` 返回小写字符串）：
Task：`submit, start, pause, resume, cancel, complete, mark_partial, mark_waiting_approval, mark_waiting_resource, delete, restore`
Node：`ready, dispatch, succeed, fail, wait_retry, retry, wait_resource, requeue, skip, block, unblock, cancel`

**稳定错误码**（`app/domain/errors.py`）：
- `IllegalTransitionError` code=`ILLEGAL_TRANSITION` status 409
- `StaleVersionError` code=`STALE_VERSION` status 409
- `IdempotencyConflictError` code=`IDEMPOTENCY_CONFLICT` status 409
- `OwnerViolationError` → 复用 `app.auth.errors.NotFoundError`（404）

**owner 复用**：`from app.auth.errors import assert_owned, NotFoundError`

---

## Task 1: 核心领域 Schema + Migration 0004

**Files:**
- Create: `backend/app/domain/__init__.py`
- Create: `backend/app/domain/errors.py`
- Create: `backend/app/domain/models.py`（全部 16 表 ORM）
- Create: `backend/alembic/versions/0004_create_domain_core.py`
- Create: `backend/tests/domain/__init__.py`
- Create: `backend/tests/domain/conftest.py`
- Create: `backend/tests/domain/test_models_roundtrip.py`

**Interfaces:**
- Consumes: `app.infra.db.Base`、`app.auth.models.User`。
- Produces: 全部 ORM 模型（下表）、`app.domain.errors` 错误族、migration `0004`。

- [ ] **Step 1: 写模型回环失败测试**

新建 `backend/tests/domain/conftest.py`：

```python
"""Shared fixtures for domain tests (SQLite)."""
from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'domain.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def user(db: DbSession) -> User:
    return UserRepository(db).create("alice@example.com", "hash", None)


@pytest.fixture()
def task(db: DbSession, user: User) -> Task:
    return TaskRepository(db).create(user_id=user.id, title="seed", task_type="directed")
```

新建 `backend/tests/domain/test_models_roundtrip.py`：

```python
"""ORM roundtrip: create core objects and read them back."""
from __future__ import annotations

from app.domain.models import (
    CollectionSpecVersion, NodeRun, PlanVersion, Record, Run, Task,
)
from app.domain.repository import (
    NodeRunRepository, RunRepository, SpecVersionRepository, TaskRepository,
)


def test_task_roundtrip(db, user):
    repo = TaskRepository(db)
    task = repo.create(user_id=user.id, title="t", task_type="directed")
    fetched = repo.get_owned(user.id, task.id)
    assert fetched.title == "t"
    assert fetched.state == "draft"
    assert fetched.version == 1


def test_run_and_spec_roundtrip(db, user, task):
    spec = SpecVersionRepository(db).create(
        user_id=user.id, task_id=task.id, version=1, spec_type="collection",
        schema_version="v1", payload={"fields": ["url", "title"]},
    )
    run = RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1,
    )
    node = NodeRunRepository(db).create(
        user_id=user.id, run_id=run.id, task_id=task.id, node_type="fetch",
        input_fingerprint="abc",
    )
    assert spec.version == 1
    assert run.state == "pending"
    assert node.state == "pending"
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_models_roundtrip.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.domain`）。

- [ ] **Step 3: 实现 errors + models**

新建 `backend/app/domain/errors.py`：

```python
"""Stable domain error taxonomy (M-04)."""
from __future__ import annotations


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class IllegalTransitionError(DomainError):
    code = "ILLEGAL_TRANSITION"
    status_code = 409


class StaleVersionError(DomainError):
    code = "STALE_VERSION"
    status_code = 409


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409
```

新建 `backend/app/domain/models.py`（16 表；状态列为 `String` 存 `.value`；乐观锁 `version`）：

```python
"""Core execution domain models (M-04).

Every business table carries an explicit NOT NULL user_id; owners are never
derived implicitly. States are stored as canonical uppercase strings (see
app.state.states). Optimistic concurrency uses ``version`` on mutable rows.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(30), nullable=False, default="directed")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_spec_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class CollectionSpecVersion(Base):
    __tablename__ = "collection_spec_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_csv_task_version"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_type: Mapped[str] = mapped_column(String(30), nullable=False, default="collection")
    schema_version: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_pv_task_version"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NodeRun(Base):
    __tablename__ = "node_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NodeAttempt(Base):
    __tablename__ = "node_attempts"
    __table_args__ = (UniqueConstraint("node_run_id", "attempt", name="uq_na_node_attempt"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_run_id: Mapped[int] = mapped_column(ForeignKey("node_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class URLResource(Base):
    __tablename__ = "url_resources"
    __table_args__ = (UniqueConstraint("task_id", "url_hash", name="uq_ur_task_url_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="seed")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    url_resource_id: Mapped[int | None] = mapped_column(ForeignKey("url_resources.id"), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="stored")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    url_resource_id: Mapped[int | None] = mapped_column(ForeignKey("url_resources.id"), nullable=True)
    business_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    partition: Mapped[str] = mapped_column(String(30), nullable=False, default="passed")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class FieldEvidence(Base):
    __tablename__ = "field_evidence"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshots.id"), nullable=True)
    extract_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parameter_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="single")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    export_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filter_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DomainEvent(Base):
    __tablename__ = "domain_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False, default="user")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("node_runs.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_ik_scope_key"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    result_ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "batch_identity", name="uq_cp_run_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("node_runs.id"), nullable=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_object_refs: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

> 注意：`db` fixture 依赖 `TaskRepository`，实现见 Task 1 Step 4（fixture 与本文件一起提交；先写 repository 再跑测试）。

- [ ] **Step 4: 实现 Task1 Repository 子集 + migration**

在 `backend/app/domain/repository.py` 实现（本 Task 先实现 roundtrip 所需子集，Task 6 补全）：

```python
"""Owner-scoped repositories for core domain objects (M-04)."""
from __future__ import annotations

from app.auth.errors import NotFoundError
from app.domain.models import CollectionSpecVersion, NodeRun, PlanVersion, Run, Task


def _owned(db, model, user_id: int, obj_id: int):
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


class TaskRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, *, user_id: int, title: str, task_type: str = "directed") -> Task:
        task = Task(user_id=user_id, title=title, task_type=task_type, state="draft", version=1)
        self._db.add(task)
        self._db.commit()
        self._db.refresh(task)
        return task

    def get_owned(self, user_id: int, task_id: int) -> Task:
        return _owned(self._db, Task, user_id, task_id)


class SpecVersionRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, *, user_id: int, task_id: int, version: int, spec_type: str,
               schema_version: str, payload: dict) -> CollectionSpecVersion:
        row = CollectionSpecVersion(
            user_id=user_id, task_id=task_id, version=version, spec_type=spec_type,
            schema_version=schema_version, payload=payload,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row


class PlanVersionRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, *, user_id: int, task_id: int, spec_version: int, version: int,
               payload: dict) -> PlanVersion:
        row = PlanVersion(user_id=user_id, task_id=task_id, spec_version=spec_version,
                          version=version, payload=payload)
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row


class RunRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, *, user_id: int, task_id: int, spec_version: int, plan_version: int) -> Run:
        row = Run(user_id=user_id, task_id=task_id, spec_version=spec_version,
                  plan_version=plan_version, state="pending")
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, run_id: int) -> Run:
        return _owned(self._db, Run, user_id, run_id)


class NodeRunRepository:
    def __init__(self, db) -> None:
        self._db = db

    def create(self, *, user_id: int, run_id: int, task_id: int, node_type: str,
               input_fingerprint: str | None = None) -> NodeRun:
        row = NodeRun(user_id=user_id, run_id=run_id, task_id=task_id, node_type=node_type,
                      input_fingerprint=input_fingerprint, state="pending", version=1)
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, node_run_id: int) -> NodeRun:
        return _owned(self._db, NodeRun, user_id, node_run_id)
```

新建 `backend/alembic/versions/0004_create_domain_core.py`：镜像 `models.py` 全部 16 表（列类型与约束完全一致），`down_revision="0003"`，`downgrade()` 逆序 drop 全部表。列类型参考 0003 风格（`sa.BigInteger`/`sa.String`/`sa.JSON`/`sa.Text`/`sa.DateTime(timezone=True)`/`sa.Float`/`sa.UniqueConstraint`/`sa.Index`/FK `ondelete="CASCADE"`）。JSON 在 PostgreSQL 映射为 `JSONB`（Alembic `sa.JSON` 自动映射）。

- [ ] **Step 5: 运行 + migration 可逆性检查**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_models_roundtrip.py -v
.venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m alembic heads
.venv/Scripts/python -m alembic downgrade 0003 && .venv/Scripts/python -m alembic upgrade head
```

Expected: 测试 PASS；head=0004；downgrade 到 0003 再 upgrade 成功（可逆）。

- [ ] **Step 6: 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/domain backend/alembic/versions/0004_create_domain_core.py backend/tests/domain
git commit -m "feat(domain): add core task execution models and schema"
```

Expected: ruff/mypy PASS；Commit 生成。

---

## Task 2: Task / Node State Machine + allowed_actions

**Files:**
- Create: `backend/app/state/__init__.py`
- Create: `backend/app/state/states.py`
- Create: `backend/tests/domain/test_state_machine.py`

**Interfaces:**
- Consumes: 无 DB 依赖（纯函数）。
- Produces: `TaskState`/`NodeState` enums、`TASK_COMMANDS`/`NODE_COMMANDS`（command→(from,to)）、`allowed_actions(state)`、`can_transition(kind, current, command) -> next`、`assert_can_transition(...)`。

- [ ] **Step 1: 写状态机失败测试**

新建 `backend/tests/domain/test_state_machine.py`：

```python
"""Canonical transition matrix + allowed_actions."""
from __future__ import annotations

import pytest
from app.domain.errors import IllegalTransitionError
from app.state.states import (
    NodeState, TaskState, allowed_node_actions, allowed_task_actions,
    assert_task_transition, assert_node_transition,
)

TASK_OK = [
    (TaskState.DRAFT, "submit", TaskState.QUEUED),
    (TaskState.QUEUED, "start", TaskState.RUNNING),
    (TaskState.RUNNING, "pause", TaskState.PAUSING),
    (TaskState.PAUSING, "cancel", TaskState.CANCELLING),
    (TaskState.PAUSED, "resume", TaskState.RUNNING),
    (TaskState.RUNNING, "cancel", TaskState.CANCELLING),
    (TaskState.CANCELLING, "cancel", TaskState.CANCELLING),  # idempotent worker stop
    (TaskState.RUNNING, "complete", TaskState.COMPLETED),
    (TaskState.RUNNING, "mark_partial", TaskState.PARTIALLY_COMPLETED),
    (TaskState.RUNNING, "fail", TaskState.FAILED),
    (TaskState.QUEUED, "cancel", TaskState.CANCELLED),
    (TaskState.DRAFT, "delete", TaskState.DELETED),
    (TaskState.COMPLETED, "delete", TaskState.DELETED),
    (TaskState.FAILED, "delete", TaskState.DELETED),
    (TaskState.DELETED, "restore", TaskState.DRAFT),
]

TASK_BAD = [
    (TaskState.RUNNING, "delete", TaskState.DELETED),
    (TaskState.RUNNING, "resume", TaskState.RUNNING),
    (TaskState.DRAFT, "start", TaskState.RUNNING),
    (TaskState.DELETED, "submit", TaskState.QUEUED),
    (TaskState.COMPLETED, "start", TaskState.RUNNING),
]


@pytest.mark.parametrize(("state", "cmd", "next"), TASK_OK)
def test_task_legal_transitions(state, cmd, next) -> None:
    assert assert_task_transition(state, cmd) == next


@pytest.mark.parametrize(("state", "cmd", "next"), TASK_BAD)
def test_task_illegal_transitions(state, cmd, next) -> None:
    with pytest.raises(IllegalTransitionError):
        assert_task_transition(state, cmd)


def test_task_allowed_actions_consistent() -> None:
    for state in TaskState:
        for action in allowed_task_actions(state):
            assert_task_transition(state, action)  # must be legal


def test_node_allowed_actions_consistent() -> None:
    for state in NodeState:
        for action in allowed_node_actions(state):
            assert_node_transition(state, action)


def test_running_task_cannot_delete() -> None:
    assert "delete" not in allowed_task_actions(TaskState.RUNNING)
    assert "delete" not in allowed_task_actions(TaskState.PAUSING)
    assert "delete" not in allowed_task_actions(TaskState.CANCELLING)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_state_machine.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.state`）。

- [ ] **Step 3: 实现 states.py**

新建 `backend/app/state/states.py`（canonical vocabulary 唯一；命令映射显式）：

```python
"""Canonical state vocabulary, transition matrix and allowed_actions (M-04).

Single source of truth for state semantics. DB stores ``TaskState.value`` /
``NodeState.value`` (uppercase). Never add a second name for the same meaning.
"""
from __future__ import annotations

from enum import StrEnum

from app.domain.errors import IllegalTransitionError


class TaskState(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class NodeState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# command -> (from_state, to_state). One source of truth for the matrix.
TASK_COMMANDS: dict[str, list[tuple[TaskState, TaskState]]] = {
    "submit": [(TaskState.DRAFT, TaskState.QUEUED)],
    "start": [(TaskState.QUEUED, TaskState.RUNNING)],
    "pause": [(TaskState.RUNNING, TaskState.PAUSING)],
    "resume": [
        (TaskState.PAUSED, TaskState.RUNNING),
        (TaskState.WAITING_APPROVAL, TaskState.RUNNING),
        (TaskState.WAITING_RESOURCE, TaskState.RUNNING),
    ],
    "cancel": [
        (TaskState.QUEUED, TaskState.CANCELLED),
        (TaskState.RUNNING, TaskState.CANCELLING),
        (TaskState.PAUSING, TaskState.CANCELLING),
        (TaskState.PAUSED, TaskState.CANCELLING),
        (TaskState.WAITING_APPROVAL, TaskState.CANCELLING),
        (TaskState.WAITING_RESOURCE, TaskState.CANCELLING),
        (TaskState.CANCELLING, TaskState.CANCELLING),
    ],
    "complete": [(TaskState.RUNNING, TaskState.COMPLETED)],
    "mark_partial": [(TaskState.RUNNING, TaskState.PARTIALLY_COMPLETED)],
    "mark_waiting_approval": [(TaskState.RUNNING, TaskState.WAITING_APPROVAL)],
    "mark_waiting_resource": [(TaskState.RUNNING, TaskState.WAITING_RESOURCE)],
    "delete": [
        (TaskState.DRAFT, TaskState.DELETED),
        (TaskState.QUEUED, TaskState.DELETED),
        (TaskState.PAUSED, TaskState.DELETED),
        (TaskState.WAITING_APPROVAL, TaskState.DELETED),
        (TaskState.WAITING_RESOURCE, TaskState.DELETED),
        (TaskState.CANCELLED, TaskState.DELETED),
        (TaskState.COMPLETED, TaskState.DELETED),
        (TaskState.PARTIALLY_COMPLETED, TaskState.DELETED),
        (TaskState.FAILED, TaskState.DELETED),
    ],
    "restore": [(TaskState.DELETED, TaskState.DRAFT)],
}

NODE_COMMANDS: dict[str, list[tuple[NodeState, NodeState]]] = {
    "ready": [(NodeState.PENDING, NodeState.READY)],
    "dispatch": [
        (NodeState.READY, NodeState.RUNNING),
        (NodeState.WAITING_RESOURCE, NodeState.READY),
    ],
    "succeed": [(NodeState.RUNNING, NodeState.SUCCEEDED)],
    "fail": [(NodeState.RUNNING, NodeState.FAILED)],
    "wait_retry": [(NodeState.RUNNING, NodeState.WAITING_RETRY)],
    "retry": [(NodeState.WAITING_RETRY, NodeState.READY)],
    "wait_resource": [(NodeState.RUNNING, NodeState.WAITING_RESOURCE)],
    "requeue": [(NodeState.WAITING_RESOURCE, NodeState.READY)],
    "skip": [(NodeState.PENDING, NodeState.SKIPPED), (NodeState.READY, NodeState.SKIPPED)],
    "block": [(NodeState.PENDING, NodeState.BLOCKED)],
    "unblock": [(NodeState.BLOCKED, NodeState.READY)],
    "cancel": [
        (NodeState.PENDING, NodeState.CANCELLED),
        (NodeState.READY, NodeState.CANCELLED),
        (NodeState.RUNNING, NodeState.CANCELLED),
        (NodeState.WAITING_RETRY, NodeState.CANCELLED),
        (NodeState.WAITING_RESOURCE, NodeState.CANCELLED),
        (NodeState.BLOCKED, NodeState.CANCELLED),
    ],
}


def _resolve(commands: dict, kind: str, state, command: str):
    for from_state, to_state in commands.get(command, []):
        if from_state == state:
            return to_state
    raise IllegalTransitionError(f"{kind} 当前状态不允许执行 {command}")


def assert_task_transition(state: TaskState, command: str) -> TaskState:
    return _resolve(TASK_COMMANDS, "任务", state, command)


def assert_node_transition(state: NodeState, command: str) -> NodeState:
    return _resolve(NODE_COMMANDS, "节点", state, command)


def allowed_task_actions(state: TaskState) -> list[str]:
    return [cmd for cmd, pairs in TASK_COMMANDS.items() if any(f == state for f, _ in pairs)]


def allowed_node_actions(state: NodeState) -> list[str]:
    return [cmd for cmd, pairs in NODE_COMMANDS.items() if any(f == state for f, _ in pairs)]
```

- [ ] **Step 4: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_state_machine.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/state backend/tests/domain && git commit -m "feat(state): add task and node transition machines"
```

Expected: 全 PASS；Commit 生成。

---

## Task 3: DomainEvent + Transactional Outbox + 原子状态转换

**Files:**
- Create: `backend/app/state/events.py`
- Modify: `backend/app/domain/repository.py`（追加 OutboxRepository + DomainEventRepository + update_state）
- Create: `backend/app/domain/service.py`
- Create: `backend/tests/domain/test_transaction_atomicity.py`

**Interfaces:**
- Consumes: Task 1 models/repo、Task 2 `assert_task_transition`/`assert_node_transition`、`app.auth.errors.assert_owned`。
- Produces: `append_domain_event(db, ...) -> DomainEvent`、`enqueue_outbox(db, ...)`、`transition_task(db, *, user_id, task_id, command, expected_version, actor, reason)`、`transition_node(db, *, user_id, node_run_id, command, expected_version, actor, reason)`、`OutboxRepository.claim_pending/mark_dispatched/mark_failed`、`NodeAttemptRepository.create`。

- [ ] **Step 1: 写原子性失败测试**

新建 `backend/tests/domain/test_transaction_atomicity.py`：

```python
"""State+event+outbox commit in one transaction; a mid-failure rolls back all."""
from __future__ import annotations

import pytest
from app.domain.models import DomainEvent, OutboxEvent, Task
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.state.events import append_domain_event, enqueue_outbox
from app.state.states import TaskState


@pytest.fixture()
def service(db) -> DomainService:
    return DomainService(TaskRepository(db))


def test_transition_writes_state_event_outbox(db, service, user, task) -> None:
    service.transition_task(
        user_id=user.id, task_id=task.id, command="submit", expected_version=1,
        actor_type="user", actor_id=user.id, reason="spec confirmed",
    )
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.QUEUED.value
    assert fresh.version == 2
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() == 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 1


def test_mid_transaction_failure_rolls_back_everything(db, service, user, task, monkeypatch) -> None:
    def _boom(db, **kwargs):
        raise RuntimeError("outbox down")

    monkeypatch.setattr("app.domain.service.enqueue_outbox", _boom)
    with pytest.raises(RuntimeError):
        service.transition_task(
            user_id=user.id, task_id=task.id, command="submit", expected_version=1,
            actor_type="user", actor_id=user.id, reason="boom",
        )
    db.rollback()
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value  # state not changed
    assert fresh.version == 1
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() == 0
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 0


def test_node_transition_and_attempt(db, service, user, task) -> None:
    from app.domain.repository import NodeRunRepository, RunRepository
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(user_id=user.id, run_id=run.id, task_id=task.id, node_type="fetch")
    service.transition_node(
        user_id=user.id, node_run_id=node.id, command="ready", expected_version=1,
        actor_type="user", actor_id=user.id, reason=None,
    )
    db.expire_all()
    fresh = NodeRunRepository(db).get_owned(user.id, node.id)
    assert fresh.state == "ready"
    assert fresh.version == 2
    from app.domain.models import NodeAttempt
    assert db.query(NodeAttempt).filter(NodeAttempt.node_run_id == node.id).count() == 1
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_transaction_atomicity.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.domain.service`）。

- [ ] **Step 3: 实现 events.py + repository 追加 + service**

新建 `backend/app/state/events.py`：

```python
"""Append-only DomainEvent + transactional Outbox enqueue (M-04).

These helpers are called inside the SAME db transaction as the state change;
caller commits once. Never UPDATE a historical event.
"""
from __future__ import annotations

from app.domain.models import DomainEvent, OutboxEvent


def append_domain_event(
    db, *, user_id: int, aggregate_type: str, aggregate_id: int, event_type: str,
    aggregate_version: int, payload: dict, actor_type: str = "user",
    actor_id: int | None = None, run_id: int | None = None,
    node_run_id: int | None = None,
) -> DomainEvent:
    row = DomainEvent(
        user_id=user_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
        event_type=event_type, aggregate_version=aggregate_version, payload=payload,
        actor_type=actor_type, actor_id=actor_id, run_id=run_id, node_run_id=node_run_id,
    )
    db.add(row)
    return row


def enqueue_outbox(
    db, *, user_id: int, aggregate_type: str, aggregate_id: int, event_type: str,
    payload: dict, dispatch_key: str | None = None,
) -> OutboxEvent:
    row = OutboxEvent(
        user_id=user_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
        event_type=event_type, payload=payload, status="pending", dispatch_key=dispatch_key,
    )
    db.add(row)
    return row
```

在 `backend/app/domain/repository.py` 追加：

```python
from app.domain.models import DomainEvent, NodeAttempt, NodeRun, OutboxEvent, Task
from app.state.states import assert_node_transition, assert_task_transition
from app.domain.errors import StaleVersionError

class NodeAttemptRepository:
    def __init__(self, db) -> None:
        self._db = db

    def next_attempt(self, node_run_id: int) -> int:
        from sqlalchemy import func, select
        latest = self._db.scalar(
            select(func.max(NodeAttempt.attempt)).where(NodeAttempt.node_run_id == node_run_id)
        )
        return (latest or 0) + 1

    def create(self, *, user_id: int, node_run_id: int, attempt: int) -> NodeAttempt:
        row = NodeAttempt(user_id=user_id, node_run_id=node_run_id, attempt=attempt, status="pending")
        self._db.add(row)
        return row

class OutboxRepository:
    def __init__(self, db) -> None:
        self._db = db

    def claim_pending(self, *, limit: int = 50) -> list[OutboxEvent]:
        from sqlalchemy import select
        return list(self._db.scalars(
            select(OutboxEvent).where(OutboxEvent.status == "pending").order_by(OutboxEvent.id).limit(limit)
        ))

    def mark_dispatched(self, outbox: OutboxEvent) -> None:
        from datetime import UTC, datetime
        outbox.status = "dispatched"
        outbox.dispatched_at = datetime.now(UTC)
        self._db.commit()

    def mark_failed(self, outbox: OutboxEvent) -> None:
        outbox.status = "failed"
        outbox.attempts += 1
        self._db.commit()

# 在 TaskRepository 追加（乐观锁 + 状态更新）：
    def update_state(self, task: Task, new_state: str, expected_version: int) -> Task:
        if task.version != expected_version:
            raise StaleVersionError("任务已被其他操作修改")
        task.state = new_state
        task.version = task.version + 1
        self._db.add(task)
        return task

# 在 NodeRunRepository 追加：
    def update_state(self, node: NodeRun, new_state: str, expected_version: int) -> NodeRun:
        if node.version != expected_version:
            raise StaleVersionError("节点已被其他操作修改")
        node.state = new_state
        node.version = node.version + 1
        self._db.add(node)
        return node
```

新建 `backend/app/domain/service.py`：

```python
"""Domain commands: transition_task / transition_node / checkpoint (M-04).

All writes for one command happen in the same db transaction and commit once;
a failure rolls back state, event and outbox together.
"""
from __future__ import annotations

from app.domain.errors import IllegalTransitionError
from app.domain.models import DomainEvent, OutboxEvent
from app.domain.repository import (
    NodeAttemptRepository, NodeRunRepository, TaskRepository,
)
from app.state.events import append_domain_event, enqueue_outbox
from app.state.states import assert_node_transition, assert_task_transition

EVENT_KIND = {"task": "task", "node_run": "node_run"}


class DomainService:
    def __init__(self, task_repo: TaskRepository, node_repo: NodeRunRepository | None = None,
                 attempt_repo: NodeAttemptRepository | None = None) -> None:
        self._tasks = task_repo
        self._nodes = node_repo or NodeRunRepository(task_repo._db)
        self._attempts = attempt_repo or NodeAttemptRepository(task_repo._db)

    def transition_task(self, *, user_id: int, task_id: int, command: str,
                        expected_version: int, actor_type: str = "user",
                        actor_id: int | None = None, reason: str | None = None) -> DomainEvent:
        db = self._tasks._db
        task = self._tasks.get_owned(user_id, task_id)
        from app.domain.models import Task
        from app.state.states import TaskState
        current = TaskState(task.state)
        try:
            next_state = assert_task_transition(current, command)
        except IllegalTransitionError:
            raise
        if task.version != expected_version:
            from app.domain.errors import StaleVersionError
            raise StaleVersionError("任务已被其他操作修改")
        payload: dict = {
            "command": command,
            "from_state": task.state,
            "to_state": next_state.value,
            "reason": reason,
        }
        if next_state == TaskState.DELETED:
            from datetime import UTC, datetime
            task.deleted_at = datetime.now(UTC)
        task.state = next_state.value
        task.version += 1
        db.add(task)
        event = append_domain_event(
            db, user_id=user_id, aggregate_type="task", aggregate_id=task_id,
            event_type=f"task.{command}", aggregate_version=task.version, payload=payload,
            actor_type=actor_type, actor_id=actor_id,
        )
        enqueue_outbox(
            db, user_id=user_id, aggregate_type="task", aggregate_id=task_id,
            event_type=f"task.{command}", payload=payload, dispatch_key=f"task:{task_id}:{command}",
        )
        db.commit()
        db.refresh(event)
        return event

    def transition_node(self, *, user_id: int, node_run_id: int, command: str,
                        expected_version: int, actor_type: str = "system",
                        actor_id: int | None = None, reason: str | None = None) -> DomainEvent:
        db = self._nodes._db
        node = self._nodes.get_owned(user_id, node_run_id)
        from app.state.states import NodeState
        current = NodeState(node.state)
        next_state = assert_node_transition(current, command)
        if node.version != expected_version:
            from app.domain.errors import StaleVersionError
            raise StaleVersionError("节点已被其他操作修改")
        payload = {"command": command, "from_state": node.state, "to_state": next_state.value,
                   "reason": reason}
        if next_state == NodeState.RUNNING:
            attempt_no = self._attempts.next_attempt(node.id)
            self._attempts.create(user_id=user_id, node_run_id=node.id, attempt=attempt_no)
        node.state = next_state.value
        node.version += 1
        db.add(node)
        event = append_domain_event(
            db, user_id=user_id, aggregate_type="node_run", aggregate_id=node_run_id,
            event_type=f"node.{command}", aggregate_version=node.version, payload=payload,
            actor_type=actor_type, actor_id=actor_id, run_id=node.run_id,
            node_run_id=node_run_id,
        )
        enqueue_outbox(
            db, user_id=user_id, aggregate_type="node_run", aggregate_id=node_run_id,
            event_type=f"node.{command}", payload=payload, dispatch_key=f"node:{node_run_id}:{command}",
        )
        db.commit()
        db.refresh(event)
        return event
```

> 实现注意：`DomainService` 通过注入的 repo 共享同一 `db` 会话，保证同事务。`transition_task` 幂等处理 `CANCELLING→CANCELLING`（matrix 允许）；终态事件照常 append。

- [ ] **Step 4: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_transaction_atomicity.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/state backend/app/domain backend/tests/domain
git commit -m "feat(event): add transactional domain event outbox and atomic transitions"
```

Expected: 全 PASS；Commit 生成。

---

## Task 4: Idempotency Identities + Repository 行为

**Files:**
- Create: `backend/app/domain/idempotency.py`
- Modify: `backend/app/domain/repository.py`（追加 IdempotencyRepository）
- Create: `backend/tests/domain/test_idempotency.py`

**Interfaces:**
- Consumes: 无。
- Produces: `canonical_json(value) -> str`、`stable_fingerprint(*parts) -> str`、`api_operation_key(operation, client_key)`、`idempotency_key_for_node(task_id, spec_version, node_type, input_fingerprint)`、`idempotency_key_for_artifact(dataset_version, export_type, filter_snapshot, content_hash)`、`IdempotencyService.record(db, user_id, operation, key, payload, result_ref) -> (replay: bool, result_ref_id)`、`IdempotencyRepository`。

- [ ] **Step 1: 写幂等失败测试**

新建 `backend/tests/domain/test_idempotency.py`：

```python
"""Stable fingerprints + idempotency record/replay/conflict."""
from __future__ import annotations

import pytest
from app.domain.errors import IdempotencyConflictError
from app.domain.idempotency import (
    IdempotencyService, idempotency_key_for_artifact, idempotency_key_for_node,
    stable_fingerprint,
)


def test_stable_fingerprint_is_deterministic() -> None:
    a = stable_fingerprint({"b": 1, "a": [2, 3]}, "x")
    b = stable_fingerprint({"a": [2, 3], "b": 1}, "x")  # key order differs
    assert a == b


def test_node_key_derived_from_semantics() -> None:
    k1 = idempotency_key_for_node(task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp1")
    k2 = idempotency_key_for_node(task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp1")
    k3 = idempotency_key_for_node(task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp2")
    assert k1 == k2 and k1 != k3


def test_artifact_key_is_stable() -> None:
    a = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash1")
    b = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash1")
    c = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash2")
    assert a == b and a != c


def test_same_key_same_payload_reuses(db, user) -> None:
    service = IdempotencyService()
    first = service.record(db, user_id=user.id, operation="task.create",
                           client_key="k-1", payload={"title": "t"}, result_ref=("task", 10))
    second = service.record(db, user_id=user.id, operation="task.create",
                            client_key="k-1", payload={"title": "t"}, result_ref=("task", 10))
    assert first == (False, 10)
    assert second == (True, 10)


def test_same_key_different_payload_conflicts(db, user) -> None:
    service = IdempotencyService()
    service.record(db, user_id=user.id, operation="task.create",
                   client_key="k-2", payload={"title": "a"}, result_ref=("task", 11))
    with pytest.raises(IdempotencyConflictError):
        service.record(db, user_id=user.id, operation="task.create",
                       client_key="k-2", payload={"title": "b"}, result_ref=("task", 12))


def test_db_unique_is_backstop(db, user) -> None:
    service = IdempotencyService()
    service.record(db, user_id=user.id, operation="task.create",
                   client_key="k-3", payload={"title": "a"}, result_ref=("task", 13))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        service.record(db, user_id=user.id, operation="task.create",
                       client_key="k-3", payload={"title": "b"}, result_ref=("task", 14))
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_idempotency.py -v
```

Expected: FAIL（`ModuleNotFoundError: app.domain.idempotency`）。

- [ ] **Step 3: 实现 idempotency.py + repository 追加**

新建 `backend/app/domain/idempotency.py`：

```python
"""Stable idempotency identities (M-04).

Keys are derived from semantic inputs via canonical JSON + SHA-256, never from
random UUIDs. The database unique constraint is the backstop; same key with a
different payload fingerprint is a conflict, never a silent reuse.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.domain.errors import IdempotencyConflictError


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    )


def _json_default(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def stable_fingerprint(*parts: Any) -> str:
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()


def api_operation_key(operation: str, client_key: str) -> str:
    return stable_fingerprint("api", operation, client_key)


def idempotency_key_for_node(task_id: int, spec_version: int, node_type: str,
                             input_fingerprint: str) -> str:
    return stable_fingerprint("node", task_id, spec_version, node_type, input_fingerprint)


def idempotency_key_for_artifact(dataset_version: str, export_type: str,
                                 filter_snapshot: Any, content_hash: str) -> str:
    return stable_fingerprint("artifact", dataset_version, export_type, filter_snapshot, content_hash)


class IdempotencyService:
    def record(self, db, *, user_id: int, operation: str, client_key: str,
               payload: Any, result_ref: tuple[str, int]) -> tuple[bool, int]:
        """Record a client idempotency key. Returns (was_replay, result_ref_id)."""
        from app.domain.models import IdempotencyKey
        from app.domain.repository import IdempotencyRepository

        key = api_operation_key(operation, client_key)
        fp = stable_fingerprint(payload)
        repo = IdempotencyRepository(db)
        existing = repo.find(user_id=user_id, operation=operation, key=key)
        if existing is not None:
            if existing.payload_fingerprint != fp:
                raise IdempotencyConflictError("相同幂等键但请求内容不同")
            return True, existing.result_ref_id if existing.result_ref_id is not None else 0
        ref_type, ref_id = result_ref
        repo.create(user_id=user_id, operation=operation, key=key,
                    payload_fingerprint=fp, result_ref_type=ref_type, result_ref_id=ref_id)
        db.commit()
        return False, ref_id
```

在 `backend/app/domain/repository.py` 追加：

```python
from app.domain.models import IdempotencyKey

class IdempotencyRepository:
    def __init__(self, db) -> None:
        self._db = db

    def find(self, *, user_id: int, operation: str, key: str) -> IdempotencyKey | None:
        from sqlalchemy import select
        return self._db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.operation == operation,
                IdempotencyKey.idempotency_key == key,
            )
        )

    def create(self, *, user_id: int, operation: str, key: str, payload_fingerprint: str,
               result_ref_type: str, result_ref_id: int) -> IdempotencyKey:
        row = IdempotencyKey(user_id=user_id, operation=operation, idempotency_key=key,
                             payload_fingerprint=payload_fingerprint,
                             result_ref_type=result_ref_type, result_ref_id=result_ref_id)
        self._db.add(row)
        return row
```

- [ ] **Step 4: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_idempotency.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/domain backend/tests/domain && git commit -m "feat(domain): add idempotency identities"
```

Expected: 全 PASS；Commit 生成。

---

## Task 5: Checkpoint 提交与重放语义

**Files:**
- Modify: `backend/app/domain/repository.py`（追加 CheckpointRepository）
- Modify: `backend/app/domain/service.py`（追加 `commit_checkpoint`）
- Create: `backend/tests/domain/test_checkpoint.py`

**Interfaces:**
- Consumes: Task 1 models。
- Produces: `CheckpointRepository.find_by_batch/create`、`DomainService.commit_checkpoint(...) -> Checkpoint`（replay 复用 / fingerprint 冲突 / 失败事务不出 checkpoint）。

- [ ] **Step 1: 写 Checkpoint 失败测试**

新建 `backend/tests/domain/test_checkpoint.py`：

```python
"""Checkpoint only after committed work; replay reuses; failed txn yields none."""
from __future__ import annotations

import pytest
from app.domain.errors import StaleVersionError
from app.domain.models import Checkpoint
from app.domain.repository import CheckpointRepository, RunRepository, TaskRepository
from app.domain.service import DomainService
from app.state.states import TaskState


@pytest.fixture()
def run(db, user, task):
    return RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)


def _commit(service, user, task, run, *, batch="b1", fp="fp1", fail=False):
    if fail:
        with pytest.raises(StaleVersionError):
            # stale version on the task forces the batch txn to fail
            service.transition_task(user_id=user.id, task_id=task.id, command="submit",
                                    expected_version=999, actor_type="user", actor_id=user.id)
        db = service._tasks._db
        db.rollback()
        return None
    service.transition_task(user_id=user.id, task_id=task.id, command="submit",
                            expected_version=1, actor_type="user", actor_id=user.id)
    db = service._tasks._db
    return service.commit_checkpoint(
        user_id=user.id, task_id=task.id, run_id=run.id, batch_identity=batch,
        spec_version=1, plan_version=1, node_run_id=None, input_fingerprint=fp,
        committed_refs={"records": [1, 2]}, content_hash="h1",
    )


def test_commit_checkpoint_after_committed_batch(db, service, user, task, run) -> None:
    cp = _commit(service, user, task, run)
    assert cp.batch_identity == "b1"
    assert db.query(Checkpoint).count() == 1


def test_replay_reuses_checkpoint(db, service, user, task, run) -> None:
    _commit(service, user, task, run)
    cp2 = _commit(service, user, task, run, batch="b1")
    assert db.query(Checkpoint).count() == 1  # no duplicate
    assert cp2.batch_identity == "b1"


def test_same_batch_different_fingerprint_conflicts(db, service, user, task, run) -> None:
    _commit(service, user, task, run)
    from app.domain.errors import DomainError
    with pytest.raises(DomainError):
        _commit(service, user, task, run, batch="b1", fp="fp-DIFFERENT")


def test_failed_transaction_produces_no_checkpoint(db, service, user, task, run) -> None:
    _commit(service, user, task, run, fail=True)
    assert db.query(Checkpoint).count() == 0
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_checkpoint.py -v
```

Expected: FAIL（`commit_checkpoint` 未定义）。

- [ ] **Step 3: 实现 CheckpointRepository + commit_checkpoint**

在 `backend/app/domain/repository.py` 追加：

```python
from app.domain.models import Checkpoint

class CheckpointRepository:
    def __init__(self, db) -> None:
        self._db = db

    def find_by_batch(self, run_id: int, batch_identity: str) -> Checkpoint | None:
        from sqlalchemy import select
        return self._db.scalar(
            select(Checkpoint).where(
                Checkpoint.run_id == run_id, Checkpoint.batch_identity == batch_identity
            )
        )

    def create(self, *, user_id: int, task_id: int, run_id: int, batch_identity: str,
               spec_version: int, plan_version: int, node_run_id: int | None,
               input_fingerprint: str, committed_object_refs: dict,
               content_hash: str | None) -> Checkpoint:
        row = Checkpoint(
            user_id=user_id, task_id=task_id, run_id=run_id, batch_identity=batch_identity,
            spec_version=spec_version, plan_version=plan_version, node_run_id=node_run_id,
            input_fingerprint=input_fingerprint, committed_object_refs=committed_object_refs,
            content_hash=content_hash,
        )
        self._db.add(row)
        return row
```

在 `backend/app/domain/service.py` 追加方法（`DomainService.__init__` 注入 `checkpoints: CheckpointRepository`）：

```python
    def commit_checkpoint(self, *, user_id: int, task_id: int, run_id: int,
                          batch_identity: str, spec_version: int, plan_version: int,
                          node_run_id: int | None, input_fingerprint: str,
                          committed_refs: dict, content_hash: str | None) -> Checkpoint:
        from app.domain.errors import DomainError
        from app.domain.repository import CheckpointRepository
        db = self._tasks._db
        repo = CheckpointRepository(db)
        existing = repo.find_by_batch(run_id, batch_identity)
        if existing is not None:
            if existing.input_fingerprint != input_fingerprint:
                raise DomainError("相同批次身份但输入指纹不同")
            return existing  # replay: reuse committed result
        row = repo.create(
            user_id=user_id, task_id=task_id, run_id=run_id, batch_identity=batch_identity,
            spec_version=spec_version, plan_version=plan_version, node_run_id=node_run_id,
            input_fingerprint=input_fingerprint, committed_object_refs=committed_refs,
            content_hash=content_hash,
        )
        db.commit()
        db.refresh(row)
        return row
```

> 注意：`commit_checkpoint` 应在批次业务事务 **COMMIT 成功后**调用（单独事务写 checkpoint），语义上代表「已提交业务进度」。

- [ ] **Step 4: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_checkpoint.py -v
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/domain backend/tests/domain && git commit -m "feat(domain): add checkpoint commit and replay"
```

Expected: 全 PASS；Commit 生成。

---

## Task 6: Ownership / 乐观并发 / Repository 契约补全

**Files:**
- Modify: `backend/app/domain/repository.py`（补全 list_by_user 等 owner-scoped 查询 + `assert_owned`）
- Create: `backend/tests/domain/test_owner_isolation.py`
- Create: `backend/tests/domain/test_optimistic_lock.py`

**Interfaces:**
- Consumes: Task 1-5 repos。
- Produces: owner-scoped 查询补全；跨用户一律 404；乐观锁冲突测试。

- [ ] **Step 1: 写 owner 隔离失败测试**

新建 `backend/tests/domain/test_owner_isolation.py`：

```python
"""Owner isolation across core domain entities (M-04)."""
from __future__ import annotations

import pytest
from app.auth import errors as aerr
from app.domain.repository import (
    NodeRunRepository, RunRepository, SpecVersionRepository, TaskRepository,
)


def test_b_cannot_read_a_task(db):
    from app.auth.repository import UserRepository
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=alice.id, title="t", task_type="directed")
    with pytest.raises(aerr.NotFoundError):
        TaskRepository(db).get_owned(bob.id, task.id)


def test_b_cannot_read_a_run_or_node(db):
    from app.auth.repository import UserRepository
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=alice.id, title="t", task_type="directed")
    run = RunRepository(db).create(user_id=alice.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(user_id=alice.id, run_id=run.id, task_id=task.id, node_type="fetch")
    with pytest.raises(aerr.NotFoundError):
        RunRepository(db).get_owned(bob.id, run.id)
    with pytest.raises(aerr.NotFoundError):
        NodeRunRepository(db).get_owned(bob.id, node.id)


def test_list_is_user_scoped(db):
    from app.auth.repository import UserRepository
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    TaskRepository(db).create(user_id=alice.id, title="a", task_type="directed")
    TaskRepository(db).create(user_id=alice.id, title="b", task_type="directed")
    bob_ids = {t.id for t in TaskRepository(db).list_by_user(bob.id)}
    assert bob_ids == set()
```

新建 `backend/tests/domain/test_optimistic_lock.py`：

```python
"""Optimistic concurrency: stale version never silently overwrites."""
from __future__ import annotations

import pytest
from app.domain.errors import StaleVersionError
from app.domain.repository import NodeRunRepository, RunRepository, TaskRepository
from app.domain.service import DomainService


def test_stale_task_update_conflicts(db, user, task) -> None:
    service = DomainService(TaskRepository(db))
    # commit wins
    service.transition_task(user_id=user.id, task_id=task.id, command="submit",
                            expected_version=1, actor_type="user", actor_id=user.id)
    with pytest.raises(StaleVersionError):
        service.transition_task(user_id=user.id, task_id=task.id, command="submit",
                                expected_version=1, actor_type="user", actor_id=user.id)


def test_two_sessions_no_silent_overwrite(db, user, task) -> None:
    from app.domain.repository import TaskRepository as TR
    from app.domain.service import DomainService as DS
    s1 = DS(TR(db))
    s1.transition_task(user_id=user.id, task_id=task.id, command="submit",
                       expected_version=1, actor_type="user", actor_id=user.id)
    # a second actor with a stale read sees version 2 but expected_version 1
    with pytest.raises(StaleVersionError):
        s1.transition_task(user_id=user.id, task_id=task.id, command="start",
                           expected_version=1, actor_type="user", actor_id=user.id)
    fresh = TR(db).get_owned(user.id, task.id)
    assert fresh.version == 2  # first write preserved


def test_node_stale_update_conflicts(db, user, task) -> None:
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(user_id=user.id, run_id=run.id, task_id=task.id, node_type="fetch")
    service = DomainService(TaskRepository(db), NodeRunRepository(db))
    service.transition_node(user_id=user.id, node_run_id=node.id, command="ready",
                            expected_version=1, actor_type="user", actor_id=user.id)
    with pytest.raises(StaleVersionError):
        service.transition_node(user_id=user.id, node_run_id=node.id, command="dispatch",
                                expected_version=1, actor_type="user", actor_id=user.id)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_owner_isolation.py tests/domain/test_optimistic_lock.py -v
```

Expected: FAIL（`list_by_user` 未定义）。

- [ ] **Step 3: 补全 repository**

在 `backend/app/domain/repository.py` 补全 owner-scoped 查询：

```python
from sqlalchemy import select

class TaskRepository:
    ...
    def list_by_user(self, user_id: int) -> list[Task]:
        return list(self._db.scalars(
            select(Task).where(Task.user_id == user_id, Task.deleted_at.is_(None)).order_by(Task.created_at.desc())
        ))
```

同时确认 `get_owned` 对所有已实现 repo（Spec/Plan/Run/Node）均已用 `_owned`（user_id 校验）。为 `CollectionSpecVersion`/`PlanVersion` 增加 `get_owned_version(db, user_id, task_id, version)` 便捷查询（owner 经显式 user_id 校验）。

- [ ] **Step 4: 运行 + 门禁 + Commit**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/ -q
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add backend/app/domain backend/tests/domain && git commit -m "test(domain): cover concurrency and owner isolation"
```

Expected: 全 PASS；Commit 生成。

---

## Task 7: M-04 Domain Smoke、文档与 Execution Record

**Files:**
- Create: `backend/tests/domain/test_domain_smoke.py`
- Create: `docs/operations/domain-state-model.md`
- Create: `docs/implementation/M-04-execution.md`

**Interfaces:**
- Consumes: 全部 M-04 产物。
- Produces: 端到端 Domain Smoke（SQLite，无 Agent/Temporal/真实网络）。

- [ ] **Step 1: 写 Domain Smoke**

新建 `backend/tests/domain/test_domain_smoke.py`（完整链）：

```python
"""M-04 Domain Smoke: create -> spec -> plan -> run -> node -> transitions ->
checkpoint -> replay -> conflict -> owner block -> failed txn rollback."""
from __future__ import annotations

import pytest
from app.auth import errors as aerr
from app.auth.repository import UserRepository
from app.domain.errors import IdempotencyConflictError, StaleVersionError
from app.domain.idempotency import IdempotencyService
from app.domain.models import (
    Checkpoint, DomainEvent, NodeRun, OutboxEvent, Record, Task,
)
from app.domain.repository import (
    NodeRunRepository, PlanVersionRepository, RecordRepository, RunRepository,
    SpecVersionRepository, TaskRepository,
)
from app.domain.service import DomainService
from app.state.states import TaskState


@pytest.fixture()
def smoke(db):
    users = UserRepository(db)
    alice = users.create("alice@example.com", "hash", None)
    bob = users.create("bob@example.com", "hash", None)
    return {
        "db": db, "alice": alice, "bob": bob,
        "tasks": TaskRepository(db), "runs": RunRepository(db),
        "nodes": NodeRunRepository(db), "specs": SpecVersionRepository(db),
        "plans": PlanVersionRepository(db), "records": RecordRepository(db),
        "service": DomainService(TaskRepository(db)),
    }


def test_domain_smoke(smoke) -> None:
    db, alice, bob = smoke["db"], smoke["alice"], smoke["bob"]

    # 1. Create task + spec v1 + plan v1 + run + node
    task = smoke["tasks"].create(user_id=alice.id, title="采集", task_type="directed")
    smoke["specs"].create(user_id=alice.id, task_id=task.id, version=1, spec_type="collection",
                          schema_version="v1", payload={"fields": ["url"]})
    smoke["plans"].create(user_id=alice.id, task_id=task.id, spec_version=1, version=1,
                          payload={"nodes": ["fetch"]})
    run = smoke["runs"].create(user_id=alice.id, task_id=task.id, spec_version=1, plan_version=1)
    node = smoke["nodes"].create(user_id=alice.id, run_id=run.id, task_id=task.id,
                                 node_type="fetch", input_fingerprint="fp-1")

    # 2. Legal transitions: task submit -> queued; node ready -> running -> succeeded
    smoke["service"].transition_task(user_id=alice.id, task_id=task.id, command="submit",
                                     expected_version=1, actor_type="user", actor_id=alice.id)
    smoke["service"].transition_node(user_id=alice.id, node_run_id=node.id, command="ready",
                                     expected_version=1, actor_type="system")
    smoke["service"].transition_node(user_id=alice.id, node_run_id=node.id, command="dispatch",
                                     expected_version=2, actor_type="system")
    smoke["service"].transition_node(user_id=alice.id, node_run_id=node.id, command="succeed",
                                     expected_version=3, actor_type="system")

    # 3. Write a record + checkpoint AFTER the committed batch
    rec = smoke["records"].create(user_id=alice.id, task_id=task.id, run_id=run.id,
                                  spec_version=1, payload={"url": "https://a.example"})
    cp = smoke["service"].commit_checkpoint(
        user_id=alice.id, task_id=task.id, run_id=run.id, batch_identity="batch-1",
        spec_version=1, plan_version=1, node_run_id=node.id, input_fingerprint="fp-1",
        committed_refs={"records": [rec.id]}, content_hash="h1",
    )
    assert db.query(Checkpoint).filter(Checkpoint.run_id == run.id).count() == 1

    # 4. Replay same batch -> reuse, no duplicate record
    smoke["service"].commit_checkpoint(
        user_id=alice.id, task_id=task.id, run_id=run.id, batch_identity="batch-1",
        spec_version=1, plan_version=1, node_run_id=node.id, input_fingerprint="fp-1",
        committed_refs={"records": [rec.id]}, content_hash="h1",
    )
    assert db.query(Record).filter(Record.task_id == task.id).count() == 1

    # 5. Stale version -> CONFLICT
    with pytest.raises(StaleVersionError):
        smoke["service"].transition_task(user_id=alice.id, task_id=task.id, command="start",
                                         expected_version=1, actor_type="user", actor_id=alice.id)

    # 6. User B cannot read A's task
    with pytest.raises(aerr.NotFoundError):
        smoke["tasks"].get_owned(bob.id, task.id)

    # 7. Idempotency: same key+payload reuse; different payload conflict
    idem = IdempotencyService()
    replay, ref = idem.record(db, user_id=alice.id, operation="task.create",
                               client_key="smoke-1", payload={"title": "t"}, result_ref=("task", task.id))
    assert replay is False
    replay2, ref2 = idem.record(db, user_id=alice.id, operation="task.create",
                                 client_key="smoke-1", payload={"title": "t"}, result_ref=("task", task.id))
    assert replay2 is True and ref2 == task.id
    with pytest.raises(IdempotencyConflictError):
        idem.record(db, user_id=alice.id, operation="task.create",
                    client_key="smoke-1", payload={"title": "DIFFERENT"}, result_ref=("task", 999))

    # 8. Failed batch -> rollback, no checkpoint, no half state
    with pytest.raises(StaleVersionError):
        smoke["service"].transition_task(user_id=alice.id, task_id=task.id, command="start",
                                         expected_version=1, actor_type="user", actor_id=alice.id)
    db.rollback()
    fresh = smoke["tasks"].get_owned(alice.id, task.id)
    assert fresh.state == TaskState.QUEUED.value  # unchanged
    assert db.query(Checkpoint).filter(Checkpoint.batch_identity == "batch-2").count() == 0

    # 9. events + outbox recorded
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() >= 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() >= 1
```

（`RecordRepository.create` 在 Task 1/6 repository.py 中实现：`create(user_id, task_id, run_id, spec_version, payload, partition="passed", business_key=None)`。）

- [ ] **Step 2: 运行 Domain Smoke**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/test_domain_smoke.py -v
```

Expected: PASS。

- [ ] **Step 3: 写运维文档**

新建 `docs/operations/domain-state-model.md`（简短）：canonical 状态词汇表、Task/Node 转换矩阵要点、allowed_actions 规则、事务原子性（state+event+outbox 同事务）、幂等键生成规则、Checkpoint 语义（COMMIT 后创建、replay 复用、heartbeat 不冒充）、owner 隔离原则、M-04 scoped 测试命令。

- [ ] **Step 4: 写 M-04 execution record**

新建 `docs/implementation/M-04-execution.md`：状态 IN_PROGRESS→DONE、Agent、Baseline Commit=`602a5c30a8270de27206063ac2e1c2ea5efd7002`（M-03 HEAD）、依赖 M-01~M-03、目标环境 local、真实测试命令与结果、明确不做。

- [ ] **Step 5: 收尾门禁**

```bash
cd backend && .venv/Scripts/python -m pytest tests/domain/ -q
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
cd .. && git add docs/ backend/tests/domain && git commit -m "docs(domain): document M-04 execution contracts"
```

Expected: 全 PASS；Commit 生成。

- [ ] **Step 6: 最终收尾检查 + Secret Scan**

```bash
git status && git log --oneline -9
git grep -niE "sk-[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_" | grep -v docs/superpowers || echo "no real key patterns"
cd backend && .venv/Scripts/python -m alembic heads
```

Expected: 工作树 clean；~7 个 M-04 Commit；无真实 Key；head=0004。

---

## Self-Review

**1. Spec coverage：**
- 16 核心表 → Task 1（models + migration 0004）。
- Task/Node 状态机 + allowed_actions → Task 2。
- DomainEvent append-only + Outbox 同事务 → Task 3。
- transition_task / transition_node 原子转换 → Task 3。
- 幂等 identity（API/node/artifact）+ DB 兜底 → Task 4。
- Checkpoint COMMIT 后创建 + replay 复用 + 失败事务无 checkpoint → Task 5。
- 乐观锁 version + owner 隔离 → Task 6。
- M-04 Domain Smoke → Task 7。
- 软删除基础（DELETED + deleted_at，运行中不可 delete）→ Task 2/3 状态机 + service。
- D-071 WAITING_RESOURCE / D-025 PAUSING/PAUSED/CANCELLING → Task 2 状态词汇表覆盖。
- M-15 永久级联清理 → 明确不做。

**2. Placeholder scan：** 无 TBD/TODO；每个 Task 含真实代码或精确接口。migration 0004 在 Task 1 标注「镜像 models.py，列类型参考 0003 风格」——实现时按 models.py 逐列生成，属机械镜像而非占位。

**3. Type consistency：**
- `TaskState`/`NodeState` 枚举在 Task 2 定义，Task 3/5/7 一致引用 `.value`。
- `assert_task_transition(state, command) -> TaskState`、`assert_node_transition(...)` 签名一致。
- `DomainService.transition_task/transition_node/commit_checkpoint` 参数在 Task 3/5/7 一致。
- `append_domain_event`/`enqueue_outbox` 签名在 Task 3 定义、service 调用一致。
- `stable_fingerprint`/`idempotency_key_for_*` 在 Task 4 定义、测试一致。
- Repository 方法名（`create/get_owned/list_by_user/update_state/next_attempt`）跨 Task 自洽。

---

## 项目专项审批（M-04）

**CHECK 1 Business Decisions：** D-004/005/007/008（Spec/Plan 版本冻结→Task 1）PASS；D-011（分层状态+事件→Task 2/3）PASS；D-013（有界重试→Node WAITING_RETRY/attempt）PASS；D-014（三类分区→Record.partition）PASS；D-015（Checkpoint→Task 5）PASS；D-016（幂等→Task 4）PASS；D-023（owner→Task 6）PASS；D-025（PAUSING/PAUSED/CANCELLING）PASS；D-027（PostgreSQL 业务事实 / Temporal 执行位置不双事实源）PASS；D-030（核心数据对象）PASS；D-036（无费用 UI）PASS；D-065（软删除 DELETED，运行中不可删）PASS；D-071（WAITING_RESOURCE）PASS。

**CHECK 2 M-01 Compatibility：** 复用 `app.infra.db.Base`/`get_db`；不触碰 Temporal/MinIO/health/OTel/compose。

**CHECK 3 M-02 Compatibility：** 复用 `User`/`Session`/`require_user`/`assert_owned`；无第二套认证。

**CHECK 4 M-03 Compatibility：** 不重写 Credential/Provider；M-04 表不含跨 owner 引用 M-03 配置（M-06 起再在 service 层做 owner 一致性校验）。

**CHECK 5 State Machine：** canonical 词汇唯一（TaskState/NodeState 枚举）；矩阵显式（TASK_COMMANDS/NODE_COMMANDS）；allowed_actions 由矩阵派生；无散落隐式状态机。

**CHECK 6 Transaction：** current state + DomainEvent + Outbox 同事务（Task 3 service 单次 commit；原子性测试覆盖中途失败全回滚）。

**CHECK 7 Idempotency：** canonical JSON + SHA-256 stable fingerprint；无 random-only；DB unique(user,operation,key) 兜底；同 key 不同 payload → IdempotencyConflictError。

**CHECK 8 Checkpoint：** 只在批次事务 COMMIT 后创建；replay 复用；heartbeat 不冒充（无 heartbeat 写入）；失败事务无 checkpoint。

**CHECK 9 Ownership：** 所有业务表 user_id NOT NULL；Repository `get_owned`/`list_by_user` owner-scoped，无默认全表读取。

**CHECK 10 Module Boundary：** 未实现 M-05 UI、M-06 Agent/Spec Editor、M-07 Workflow、M-08 Plan 生成/Approval 命令、M-09 Crawl、M-12 Quality、M-15 CSV/级联删除。

**CHECK 11 A-Lite Testing：** 状态机 parameterized matrix；原子性/乐观锁/幂等/checkpoint/owner 各有代表测试；无全量 pytest、无 Browser E2E、无压力、无每表 CRUD 堆砌。

**CHECK 12 Git：** 7 个 Commit，每个可独立验证；不 push/merge/tag/deploy。

---

PLAN SELF-APPROVAL: PASS

business decisions: PASS
M-01 compatibility: PASS
M-02 compatibility: PASS
M-03 compatibility: PASS
domain model scope: PASS
state machine: PASS
transaction atomicity: PASS
idempotency: PASS
checkpoint semantics: PASS
ownership: PASS
module boundary: PASS
A-Lite testing: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
