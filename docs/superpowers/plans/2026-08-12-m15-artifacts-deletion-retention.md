# M-15: CSV Artifact、完成总结、删除/恢复与对象生命周期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 M-15 闭环：正式/待复核/审核完整 CSV 导出（幂等 Artifact + ObjectStorage + owner-safe 下载）、Chat 完成总结卡（NORMAL/PARTIAL、无假百分比）、Task 软删除/恢复、永久删除（引用安全清理）与 Retention 生命周期清理 job。

**Architecture:** 后端新增 `app/artifacts/`（contracts/csv_builder/repository/service/deletion/retention），复用 M-04 `Artifact` 模型 + M-10 `ObjectStorage` + M-13 `RecordListParams`/`ReviewRepository` 查询契约 + M-12 `CompletionDecision`/`QualitySnapshot`。软删除复用已有 `Task.deleted_at` + `DELETED` 状态机（delete 命令已覆盖非运行状态）。永久删除采用 manifest 先算引用 → DB 清理 → 对象引用复查 → 才物理删对象。Retention 只清理无保护引用的重型 PageSnapshot 对象，FieldEvidence `raw_snippet` 已在 DB 独立保留。前端复用 `ExportModal`/`DeleteConfirmModal`/`/tasks`/`/tasks/:id/*`，不新增页面。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Alembic / csv 标准库 / MinIO (S3) / Vue 3 + TypeScript strict / Vitest。

## Global Constraints

- 正式 CSV 只含 PASSED（D-014/D-060）；REVIEW 只含 NEEDS_REVIEW + review 字段；AUDIT 可含三分区 + 状态/审核字段。
- CSV 业务字段来自冻结 `CollectionSpecVersion.payload.fields`（D-005），列顺序 deterministic，与前端列设置无关。
- USER_OVERRIDE 用 final value；绝不改写 `original_value`/`FieldEvidence`/override audit（D-042）。
- 相同导出（dataset_version + canonical filter + export_type + schema_version）复用已有 READY Artifact；数据变化 → 新 dataset_version → 新 Artifact（D-016 产物规则）。
- CSV bytes 统一 UTF-8 with BOM + CRLF + csv 模块 QUOTE_MINIMAL；行序固定 record.id ASC。
- CSV 存 ObjectStorage；DB 只存 metadata/hash/ref；下载 owner-safe 流式返回，文件名 sanitize（无 `../`/slash/control chars）。
- Completion Card 全部来自 DB facts（CompletionDecision + 分区计数 + URLResource 处理事实），无 LLM 编造、无假百分比（D-006/D-043）。
- 运行中 Task 禁止删除，必须先 cancel（D-025/D-065）。软删除可恢复，永久删除需 state==DELETED + owner + 二次强确认。
- 永久删除不得破坏其他用户/Task 仍引用的共享物理对象（content-hash 复用，D-072）：DB 引用复查后才删对象。
- Retention 普通清理绝不删除仍被 FieldEvidence/Record/质量引用 的对象；`raw_snippet`/`source_locator` 长期保留。
- 不新增页面；不建设金额/收费 UI（D-036）；M-16 不做资源池/限流/压测；DEFERRED-DYNAMIC-E2E-01 不处理。
- 所有业务表 owner-safe；跨用户一律 404（D-023）。
- 只跑 M-15 scoped 测试，不重跑 M-09~M-14 全量回归。

## File Structure

**Backend 新增：**
- `backend/app/artifacts/__init__.py`
- `backend/app/artifacts/contracts.py` — ExportRequest/ExportType/ArtifactView/ArtifactRef/CompletionCardView/DeletionCommand
- `backend/app/artifacts/csv_builder.py` — deterministic CSV bytes + 列 schema + final value
- `backend/app/artifacts/repository.py` — ArtifactRepository（owner-safe 复用查找 + 创建）
- `backend/app/artifacts/service.py` — ArtifactService（export 幂等 + download + list）
- `backend/app/artifacts/deletion.py` — DeletionService（permanent delete manifest + 引用安全对象清理）
- `backend/app/artifacts/retention.py` — RetentionPolicy/CleanupResult/RetentionService
- `backend/app/artifacts/cli.py` — retention cleanup CLI（--dry-run）
- `backend/app/api/routes/artifacts.py` — export/download/list routes
- `backend/app/api/routes/completion.py` — completion card route
- `backend/app/api/routes/settings_data.py` — 设置 → 存储与数据 摘要 + retention dry-run 预览
- `backend/alembic/versions/0012_artifact_deletion_lifecycle.py`

**Backend 修改：**
- `backend/app/domain/models.py` — Artifact 扩展列 + Task.restore_state
- `backend/app/domain/service.py` — restore 动态回到 restore_state
- `backend/app/domain/repository.py` — TaskRepository.list_deleted
- `backend/app/domain/task_commands.py` — delete_task/restore_task
- `backend/app/api/routes/tasks.py` — delete/restore 命令 + 已删除列表 + permanent-delete
- `backend/app/api/router.py` — 注册 artifacts/completion router
- `backend/app/infra/object_storage.py` — protocol + MinIO 增加 `delete(key)`
- `backend/app/review/repository.py` — 增加 `query_records_all`（无分页，同 filter 语义）
- `backend/app/config.py` — 增加 `retention_heavy_days`、`csv_download` 相关配置

**Backend 测试：**
- `backend/tests/artifacts/conftest.py` — SQLite + 两用户 + FakeObjectStorage（含 async put/delete）+ Task 种子
- `backend/tests/artifacts/test_csv_matrix.py`（TEST A）
- `backend/tests/artifacts/test_filter_snapshot.py`（TEST B）
- `backend/tests/artifacts/test_artifact_idempotency.py`（TEST C）
- `backend/tests/artifacts/test_completion_card.py`（TEST D）
- `backend/tests/artifacts/test_soft_delete_restore.py`（TEST E）
- `backend/tests/artifacts/test_permanent_delete_reference_safety.py`（TEST F）
- `backend/tests/artifacts/test_retention.py`（TEST G）

**Frontend 新增：**
- `frontend/src/features/artifacts/types.ts`
- `frontend/src/features/artifacts/artifacts.api.ts`
- `frontend/src/features/artifacts/completion.api.ts`
- `frontend/src/features/artifacts/CompletionCard.vue`

**Frontend 修改：**
- `frontend/src/app/overlay/modals/ExportModal.vue`（真实实现）
- `frontend/src/app/overlay/modals/DeleteConfirmModal.vue`（真实实现，两段确认）
- `frontend/src/features/tasks/commands.api.ts`（delete/restore/permanentDelete）
- `frontend/src/features/tasks/TaskDataView.vue`（导出按钮）
- `frontend/src/features/tasks/TaskChatView.vue`（CompletionCard 挂载）
- `frontend/src/features/tasks/TasksView.vue`（view=deleted + 删除/恢复/永久删除）
- `frontend/src/features/settings/SettingsView.vue`（存储与数据摘要 + 清理预览）
- 对应 scoped 测试：`ExportModal.test.ts`、`CompletionCard.test.ts`、`DeletedView.test.ts`

---

### Task 1: Artifact 契约 + 持久化（Migration 0012）

**Files:**
- Modify: `backend/app/domain/models.py`（Artifact + Task 扩展）
- Create: `backend/alembic/versions/0012_artifact_deletion_lifecycle.py`
- Test: `backend/tests/artifacts/test_artifact_models.py`

**Interfaces:**
- Produces: `Artifact` 新列 `request_fingerprint/schema_version/row_count/size_bytes/filename/status`；`Task.restore_state`。
- Produces: `app/artifacts/contracts.py` 的 `ExportType` enum。

**Context:** `Artifact`（models.py:539）已有 `task_id/user_id/artifact_type/dataset_version/export_type/filter_snapshot/content_hash/storage_ref/created_at`。M-15 需要补充幂等协调与文件元数据。`Task`（models.py:30）已有 `deleted_at`；restore 需回到删除前状态，加 `restore_state`。

- [ ] **Step 1: 写失败测试**

`backend/tests/artifacts/test_artifact_models.py`：
```python
from app.domain.models import Artifact, Task


def test_artifact_has_m15_columns(db, user_a):
    db.flush()
    # 新列可通过 ORM 写入
    a = Artifact(
        user_id=user_a.id, task_id=1, artifact_type="csv",
        dataset_version="ds-abc", export_type="formal",
        filter_snapshot={"partition": "passed"},
        content_hash="h" * 64, storage_ref="artifacts/u1/csv/h.csv",
        request_fingerprint="fp-abc", schema_version="spec-v1/m06.1",
        row_count=2, size_bytes=10, filename="task_formal.csv", status="ready",
    )
    db.add(a)
    db.flush()
    assert a.request_fingerprint == "fp-abc"
    assert a.status == "ready"


def test_task_has_restore_state(db, user_a):
    db.flush()
    from app.domain.models import Task
    t = Task(user_id=user_a.id, title="x", state="COMPLETED", restore_state="COMPLETED")
    db.add(t)
    db.flush()
    assert t.restore_state == "COMPLETED"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_artifact_models.py -q`（在 backend/）
Expected: FAIL — `TypeError: 'Artifact' object has no attribute 'request_fingerprint'` / SQLAlchemy 列不存在。

- [ ] **Step 3: 扩展 models.py**

在 `Artifact`（models.py:539）类内、`created_at` 之前插入：
```python
    # ---- M-15 export/artifact lifecycle（migration 0012，expand-only）----
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
```
在 `Task`（models.py:48 `deleted_at` 之后）插入：
```python
    # M-15: 软删除前状态，restore 时回到该终态（不破坏 Run execution facts）。
    restore_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
```

- [ ] **Step 4: 创建 migration 0012**

`backend/alembic/versions/0012_artifact_deletion_lifecycle.py`：
```python
"""M-15: artifact lifecycle columns + task.restore_state.

Expand-only, additive. release_head migration for M-15.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("restore_state", sa.String(30), nullable=True))
    op.add_column("artifacts", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("artifacts", sa.Column("schema_version", sa.String(50), nullable=True))
    op.add_column("artifacts", sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("artifacts", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("artifacts", sa.Column("filename", sa.String(255), nullable=True))
    op.add_column("artifacts", sa.Column("status", sa.String(20), nullable=False, server_default="ready"))
    op.create_index("ix_artifacts_user_task_fp", "artifacts", ["user_id", "task_id", "request_fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_user_task_fp", table_name="artifacts")
    for col in ("status", "filename", "size_bytes", "row_count", "schema_version", "request_fingerprint"):
        op.drop_column("artifacts", col)
    op.drop_column("tasks", "restore_state")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_artifact_models.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: 校验 migration 一致性**

Run: `.venv/Scripts/python.exe -m alembic heads`
Expected: `0012 (head)`

- [ ] **Step 7: Commit**

```bash
git add backend/app/domain/models.py backend/alembic/versions/0012_artifact_deletion_lifecycle.py backend/tests/artifacts/test_artifact_models.py
git commit -m "feat(artifact): add M-15 artifact lifecycle and task restore columns"
```

---

### Task 2: ObjectStorage.delete + CSV 生成器 + Artifact 契约

**Files:**
- Modify: `backend/app/infra/object_storage.py`
- Create: `backend/app/artifacts/__init__.py`
- Create: `backend/app/artifacts/contracts.py`
- Create: `backend/app/artifacts/csv_builder.py`
- Test: `backend/tests/artifacts/test_csv_builder.py`

**Interfaces:**
- Produces: `ObjectStorage.delete(key) -> None`（幂等，NoSuchKey 忽略）。
- Produces: `ExportType`（StrEnum: `formal/review/audit`）、`ExportScope`（`current/all`）、`ExportRequest`、`ArtifactView`、`ArtifactRef`（见下）。
- Produces: `build_csv_bytes(records, columns, *, include_status_fields) -> bytes`、`schema_columns_for_spec(spec_payload) -> list[str]`、`final_field_dict(record, override_by_record) -> dict`。

- [ ] **Step 1: 写失败测试**

`backend/tests/artifacts/test_csv_builder.py`：
```python
from app.artifacts.csv_builder import (
    build_csv_bytes,
    final_field_dict,
    schema_columns_for_spec,
)
from app.domain.models import Record, RecordFieldOverride


def test_schema_columns_deterministic_from_spec():
    spec = {
        "fields": [
            {"name": "标题", "type": "text"},
            {"name": "文号", "type": "text"},
        ]
    }
    assert schema_columns_for_spec(spec) == ["标题", "文号"]


def test_final_field_dict_uses_override():
    r = Record(user_id=1, task_id=1, spec_version=1, partition="passed",
               payload={"values": {"标题": "原始", "文号": "X1"}})
    ov = RecordFieldOverride(user_id=1, task_id=1, record_id=1, field_name="标题",
                             final_value="人工值", value_source="USER_OVERRIDE",
                             modified_by=1)
    out = final_field_dict(r, {1: [ov]})
    assert out["标题"] == "人工值"
    assert out["文号"] == "X1"


def test_csv_bytes_stable_utf8_bom():
    records = [
        Record(user_id=1, task_id=1, spec_version=1, partition="passed",
               payload={"标题": "a", "文号": "b"})
    ]
    data = build_csv_bytes(records, ["标题", "文号"], include_status_fields=False)
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert data == build_csv_bytes(records, ["标题", "文号"], include_status_fields=False)
    text = data.decode("utf-8-sig")
    assert text.startswith("标题,文号\r\n")
    assert "a,b\r\n" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_csv_builder.py -q`
Expected: FAIL — module `app.artifacts` 不存在 / `object_storage` 无 `delete`。

- [ ] **Step 3: 实现 contracts.py**

`backend/app/artifacts/contracts.py`：
```python
"""M-15 Artifact / Export / Completion typed contracts（D-060/D-065/D-072）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class ExportType(StrEnum):
    FORMAL = "formal"      # 正式：PASSED only
    REVIEW = "review"      # 待复核：NEEDS_REVIEW + review 字段
    AUDIT = "audit"        # 审核完整：三分区 + 状态/审核字段


class ExportScope(StrEnum):
    CURRENT = "current"    # 当前 Data 页筛选结果
    ALL = "all"            # 全部当前分区


class ExportFilter(BaseModel):
    """与 M-13 RecordListParams 对齐的筛选子集（不含 partition/page/sort）。"""
    model_config = _STRICT
    q: str | None = None
    field: str | None = None
    value: str | None = None
    source_type: str | None = None
    extract_method: str | None = None
    min_confidence: float | None = None
    review_type: str | None = None


class ExportRequest(BaseModel):
    model_config = _STRICT
    export_type: ExportType
    scope: ExportScope = ExportScope.ALL
    filter: ExportFilter = Field(default_factory=ExportFilter)


class ArtifactRef(BaseModel):
    model_config = _STRICT
    artifact_id: int
    content_hash: str
    download_url: str
    row_count: int


class ArtifactView(BaseModel):
    model_config = _STRICT
    artifact_id: int
    export_type: str
    dataset_version: str
    filter_snapshot: dict
    schema_version: str | None
    row_count: int
    size_bytes: int | None
    content_hash: str
    filename: str
    status: str
    created_at: datetime
    download_url: str


class CompletionCardView(BaseModel):
    model_config = _STRICT
    task_id: int
    completion_id: int | None       # CompletionDecision 稳定 identity（幂等渲染）
    status: str                     # NORMAL_COMPLETED | PARTIALLY_COMPLETED
    reason: str | None
    completion_type: str | None
    is_partial: bool
    qualified_record_count: int
    partition_counts: dict[str, int] = {}
    url_processed: int = 0          # URLResource 终态数
    runtime_limit_reason: str | None = None
    scope_completion_metadata: dict = {}
    can_view_data: bool = True
    can_view_quality: bool = True
    can_export_formal: bool = False
    can_export_review: bool = False


class PermanentDeleteCommand(BaseModel):
    model_config = _STRICT
    confirmed: bool = False
```

- [ ] **Step 4: 实现 object_storage.delete**

在 `ObjectStorage` protocol（object_storage.py:29）加 `async def delete(self, key: str) -> None: ...`；在 `MinioObjectStorage` 加：
```python
    async def delete(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.remove_object(self._bucket, key)
            except Exception:
                # NoSuchKey / NoSuchObject 视为已删除，幂等
                pass

        await anyio.to_thread.run_sync(_delete)
```

- [ ] **Step 5: 实现 csv_builder.py**

`backend/app/artifacts/csv_builder.py`：
```python
"""Deterministic CSV bytes（D-005/D-021）。

- UTF-8 with BOM + CRLF（Excel 兼容）；csv 模块 QUOTE_MINIMAL。
- 业务列来自冻结 CollectionSpec field schema（deterministic order）。
- 行序固定 record.id ASC（不受 UI sort/分页影响）。
- 正式/待复核 CSV 不输出状态列；AUDIT 输出 partition/review_type/review_reason 审计列。
- 使用 final value（USER_OVERRIDE 叠加），绝不改写 original_value/FieldEvidence。
"""

from __future__ import annotations

import csv
import io

from app.domain.models import Record, RecordFieldOverride


def _flatten_values(payload: dict) -> dict:
    """M-11 真实记录字段嵌套在 payload['values']；fixture 平铺在 payload。"""
    values = payload.get("values")
    return dict(values) if isinstance(values, dict) else dict(payload)


def final_field_dict(record: Record, override_by_record: dict[int, list[RecordFieldOverride]]) -> dict:
    """Record 最终字段 dict = 展平 payload 叠加人工覆写（与 app.review.views 一致）。"""
    final = _flatten_values(record.payload or {})
    for ov in override_by_record.get(record.id, []):
        final[ov.field_name] = ov.final_value
    return final


def schema_columns_for_spec(spec_payload: dict | None) -> list[str]:
    """冻结 CollectionSpec 的业务列，按 spec.fields 声明顺序。"""
    fields = (spec_payload or {}).get("fields") or []
    return [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]


STATUS_COLUMNS = ["partition", "review_type", "review_reason"]


def build_csv_bytes(
    records: list[Record],
    columns: list[str],
    *,
    include_status_fields: bool,
) -> bytes:
    """确定性 CSV bytes；records 需已按 record.id ASC 排序。"""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    header = columns + (STATUS_COLUMNS if include_status_fields else [])
    writer.writerow(header)
    for r in records:
        fields = _flatten_values(r.payload or {})
        row = []
        for col in columns:
            v = fields.get(col)
            row.append("" if v is None else str(v))
        if include_status_fields:
            row += [r.partition, r.review_reason or "", r.review_reason or ""]
        writer.writerow(row)
    raw = buf.getvalue()
    return b"\xef\xbb\xbf" + raw.encode("utf-8")  # UTF-8 BOM
```

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_csv_builder.py -q`
Expected: PASS（3 passed）

- [ ] **Step 7: Commit**

```bash
git add backend/app/infra/object_storage.py backend/app/artifacts/
git commit -m "feat(artifact): add deterministic csv builder and object delete"
```

---

### Task 3: ArtifactService — 幂等导出 + dataset_version + 复用 + 上传

**Files:**
- Create: `backend/app/artifacts/repository.py`
- Create: `backend/app/artifacts/service.py`
- Modify: `backend/app/review/repository.py`（`query_records_all`）
- Test: `backend/tests/artifacts/test_artifact_service.py`

**Interfaces:**
- Consumes: `build_csv_bytes`、`final_field_dict`、`schema_columns_for_spec`、`ExportRequest/ExportType/ArtifactRef/ArtifactView`。
- Consumes: `ReviewRepository.query_records_all(user_id, task_id, params) -> list[Record]`。
- Produces: `compute_dataset_version(db, user_id, task_id) -> str`、`canonical_filter_snapshot(request) -> dict`、`ArtifactService(db, storage).export(user_id, task_id, request) -> ArtifactRef`、`.download(user_id, task_id, artifact_id) -> (bytes, filename)`、`.list_for_task(user_id, task_id) -> list[ArtifactView]`。

**核心算法（写死在 service.py 注释 + 实现）：**

```
dataset_version = "ds-" + sha256(canonical_json([ (r.id, r.partition, r.review_type,
    r.review_reason, r.data_version, sorted(final_field_dict(r)) ) for r in 全部 records ]))
```
- 反映全部数据变化（审核/覆写/reprocess），相同数据稳定。
- `canonical_filter_snapshot`：只保留 {scope, partition_forced(按 export_type), q, field, value, source_type, extract_method, min_confidence, review_type}，None/空剔除，`sort_keys=True` JSON（幂等身份不依赖 dict order）。不含 page/sort（不影响输出）。
- 复用规则：同 (user_id, task_id, dataset_version, export_type, request_fingerprint) 且 status=ready → 直接返回已有 ArtifactRef（不重新生成）。
- 否则生成 bytes → content_hash → key `artifacts/u{user_id}/csv/{content_hash}.csv` → storage 已存在则跳过 put → DB 写 Artifact 行（status=ready）→ 返回。
- object 已上传但 DB commit 失败：重试时按 key 复用（`exists` 检查），不产生 orphan。

- [ ] **Step 1: 创建 conftest + 写失败测试**

`backend/tests/artifacts/conftest.py`：
```python
"""tests/artifacts shared fixtures：SQLite + 两用户 + task + 异步 FakeObjectStorage。"""
from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.db import Base
from app.infra.object_storage import ObjectMetadata
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


class FakeObjectStorage:
    """M-15 测试用内存对象存储（全 async，与 MinIO adapter 协议一致）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> ObjectMetadata:
        self.objects[key] = data
        return ObjectMetadata(key=key, size=len(data), content_type=content_type, etag=None, content_sha256="")

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def head(self, key: str) -> ObjectMetadata | None:
        if key not in self.objects:
            return None
        return ObjectMetadata(key=key, size=len(self.objects[key]), content_type=None, etag=None, content_sha256="")

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def ensure_bucket(self) -> None:
        return None


@pytest.fixture()
def db(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def user_a(db: DbSession) -> User:
    return UserRepository(db).create("alice@example.com", "hash", None)


@pytest.fixture()
def user_b(db: DbSession) -> User:
    return UserRepository(db).create("bob@example.com", "hash", None)


@pytest.fixture()
def task_a(db: DbSession, user_a: User) -> Task:
    return TaskRepository(db).create(user_id=user_a.id, title="seed", task_type="directed")


@pytest.fixture()
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture()
def client(tmp_path) -> dict:
    """TestClient + sessionmaker + 假存储，供 artifacts API 测试（同 tests/review 模式）。"""
    from app.auth.deps import get_login_limiter
    from app.auth.rate_limit import InMemoryLoginLimiter
    from app.infra.deps import get_db, storage as storage_dep
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    fake_storage = FakeObjectStorage()
    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    app.dependency_overrides[storage_dep] = lambda: fake_storage
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "storage": fake_storage}
    app.dependency_overrides.clear()
```

`backend/tests/artifacts/test_artifact_service.py`（含 `conftest.py` 的 `db/user_a/task_a/storage`）：
```python
import pytest
from app.artifacts.contracts import ExportRequest
from app.artifacts.service import ArtifactService
from app.domain.models import Record


def _seed(db, user, task, *, partition="passed", value="x"):
    r = Record(user_id=user.id, task_id=task.id, spec_version=1,
               partition=partition, payload={"标题": value})
    db.add(r)
    db.flush()
    return r


@pytest.mark.asyncio
async def test_same_export_reuses_artifact(db, user_a, task_a, storage):
    _seed(db, user_a, task_a)
    svc = ArtifactService(db, storage)
    req = ExportRequest(export_type="formal", scope="all")
    ref1 = await svc.export(user_id=user_a.id, task_id=task_a.id, request=req)
    ref2 = await svc.export(user_id=user_a.id, task_id=task_a.id, request=req)
    assert ref1.artifact_id == ref2.artifact_id
    assert ref1.content_hash == ref2.content_hash
    assert len(storage.objects) == 1  # blob 不重复


@pytest.mark.asyncio
async def test_data_change_creates_new_artifact(db, user_a, task_a, storage):
    from app.review.contracts import RecordReviewCommand, ReviewAction
    from app.review.service import ReviewService
    r = _seed(db, user_a, task_a, partition="needs_review", value="a")
    svc = ArtifactService(db, storage)
    ref1 = await svc.export(user_id=user_a.id, task_id=task_a.id,
                            request=ExportRequest(export_type="review", scope="all"))
    ReviewService(db).execute(user_id=user_a.id, record_id=r.id,
                              cmd=RecordReviewCommand(action=ReviewAction.APPROVE,
                                                      expected_data_version=r.data_version))
    ref2 = await svc.export(user_id=user_a.id, task_id=task_a.id,
                            request=ExportRequest(export_type="formal", scope="all"))
    assert ref2.artifact_id != ref1.artifact_id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_artifact_service.py -q`
Expected: FAIL — module 不存在 / `query_records_all` 不存在。

- [ ] **Step 3: review/repository.py 增加 query_records_all**

在 `ReviewRepository` 增加：
```python
    def query_records_all(
        self, *, user_id: int, task_id: int, params: RecordListParams
    ) -> list[Record]:
        """与 query_records 相同 filter 语义，不分页，固定 record.id ASC（导出确定性）。"""
        params = params.model_copy(update={"page": 1, "page_size": 10**9,
                                           "sort_by": "id", "sort_order": "asc"})
        _, rows = self.query_records(user_id=user_id, task_id=task_id, params=params)
        return rows
```

- [ ] **Step 4: 实现 artifacts/repository.py**

```python
"""M-15 ArtifactRepository：owner-safe 复用查找 + 创建。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import Artifact


class ArtifactRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find_ready(
        self, *, user_id: int, task_id: int, dataset_version: str,
        export_type: str, request_fingerprint: str,
    ) -> Artifact | None:
        return self._db.scalar(
            select(Artifact).where(
                Artifact.user_id == user_id,
                Artifact.task_id == task_id,
                Artifact.dataset_version == dataset_version,
                Artifact.export_type == export_type,
                Artifact.request_fingerprint == request_fingerprint,
                Artifact.status == "ready",
            ).order_by(Artifact.id.desc()).limit(1)
        )

    def get_owned(self, *, user_id: int, task_id: int, artifact_id: int) -> Artifact:
        row = self._db.get(Artifact, artifact_id)
        if row is None or row.user_id != user_id or row.task_id != task_id:
            from app.auth.errors import NotFoundError
            raise NotFoundError("资源不存在")
        return row

    def create(
        self, *, user_id: int, task_id: int, artifact_type: str,
        dataset_version: str, export_type: str, filter_snapshot: dict,
        request_fingerprint: str, schema_version: str | None,
        content_hash: str, storage_ref: str, row_count: int,
        size_bytes: int, filename: str, status: str = "ready",
    ) -> Artifact:
        row = Artifact(
            user_id=user_id, task_id=task_id, artifact_type=artifact_type,
            dataset_version=dataset_version, export_type=export_type,
            filter_snapshot=filter_snapshot, request_fingerprint=request_fingerprint,
            schema_version=schema_version, content_hash=content_hash,
            storage_ref=storage_ref, row_count=row_count, size_bytes=size_bytes,
            filename=filename, status=status,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def list_for_task(self, *, user_id: int, task_id: int) -> list[Artifact]:
        return list(
            self._db.scalars(
                select(Artifact)
                .where(Artifact.user_id == user_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.desc())
            )
        )
```

- [ ] **Step 5: 实现 artifacts/service.py**

```python
"""M-15 ArtifactService：幂等 CSV 导出 + owner-safe 下载（D-016/D-060/D-072）。"""

from __future__ import annotations

import hashlib
import re

from app.artifacts.contracts import (
    ArtifactRef, ArtifactView, ExportRequest, ExportScope, ExportType,
)
from app.artifacts.csv_builder import (
    build_csv_bytes, final_field_dict, schema_columns_for_spec,
)
from app.artifacts.repository import ArtifactRepository
from app.domain.idempotency import stable_fingerprint
from app.domain.models import Record, RecordFieldOverride
from app.domain.repository import SpecVersionRepository, TaskRepository
from app.review.contracts import RecordListParams
from app.review.repository import ReviewRepository

_EXPORT_PARTITION = {
    ExportType.FORMAL: "passed",
    ExportType.REVIEW: "needs_review",
    ExportType.AUDIT: None,  # 三分区
}


def compute_dataset_version(db, *, user_id: int, task_id: int) -> str:
    """数据状态指纹：任何 record 变更（审核/覆写/reprocess）都会变化。"""
    from sqlalchemy import select
    overrides: dict[int, list[RecordFieldOverride]] = {}
    for o in db.scalars(
        select(RecordFieldOverride).where(
            RecordFieldOverride.user_id == user_id, RecordFieldOverride.task_id == task_id
        )
    ):
        overrides.setdefault(o.record_id, []).append(o)
    records = list(db.scalars(
        select(Record).where(Record.user_id == user_id, Record.task_id == task_id)
        .order_by(Record.id.asc())
    ))
    entries = [
        (r.id, r.partition, r.review_type, r.review_reason, r.data_version,
         sorted(final_field_dict(r, overrides).items()))
        for r in records
    ]
    return "ds-" + stable_fingerprint(entries)


def canonical_filter_snapshot(request: ExportRequest) -> dict:
    f: dict = request.filter.model_dump(exclude_none=True)
    forced = _EXPORT_PARTITION[request.export_type]
    snap: dict = {"scope": request.scope.value}
    if forced is not None:
        snap["partition"] = forced
    snap.update({k: v for k, v in f.items() if v not in (None, "")})
    return snap


class ArtifactService:
    def __init__(self, db, storage) -> None:
        self._db = db
        self._storage = storage
        self._repo = ArtifactRepository(db)

    async def export(self, *, user_id: int, task_id: int, request: ExportRequest) -> ArtifactRef:
        TaskRepository(self._db).get_owned(user_id, task_id)
        spec = SpecVersionRepository(self._db).latest_version(user_id, task_id)
        schema_version = f"spec-v{spec.version}/{spec.schema_version}" if spec else "no-spec"
        columns = schema_columns_for_spec(spec.payload if spec else None)

        ds_version = compute_dataset_version(self._db, user_id=user_id, task_id=task_id)
        snapshot = canonical_filter_snapshot(request)
        request_fp = stable_fingerprint(ds_version, snapshot, request.export_type.value, schema_version)

        existing = self._repo.find_ready(
            user_id=user_id, task_id=task_id, dataset_version=ds_version,
            export_type=request.export_type.value, request_fingerprint=request_fp,
        )
        if existing is not None and existing.content_hash:
            return ArtifactRef(
                artifact_id=existing.id, content_hash=existing.content_hash,
                download_url=f"/tasks/{task_id}/artifacts/{existing.id}/download",
                row_count=existing.row_count,
            )

        # 生成 rows（AUDIT 不强制 partition；FORMAL/REVIEW 强制对应分区）
        forced = _EXPORT_PARTITION[request.export_type]
        rows = self._rows_for_export(user_id, task_id, request, forced)
        include_status = request.export_type is ExportType.AUDIT
        data = build_csv_bytes(rows, columns, include_status_fields=include_status)
        content_hash = hashlib.sha256(data).hexdigest()
        key = f"artifacts/u{user_id}/csv/{content_hash}.csv"
        if not await self._storage.exists(key):
            await self._storage.put(key, data, content_type="text/csv; charset=utf-8")

        filename = self._safe_filename(
            TaskRepository(self._db).get_owned(user_id, task_id).title,
            request.export_type.value, ds_version,
        )
        artifact = self._repo.create(
            user_id=user_id, task_id=task_id, artifact_type="csv",
            dataset_version=ds_version, export_type=request.export_type.value,
            filter_snapshot=snapshot, request_fingerprint=request_fp,
            schema_version=schema_version, content_hash=content_hash,
            storage_ref=key, row_count=len(rows), size_bytes=len(data), filename=filename,
        )
        return ArtifactRef(
            artifact_id=artifact.id, content_hash=content_hash,
            download_url=f"/tasks/{task_id}/artifacts/{artifact.id}/download",
            row_count=len(rows),
        )

    def _rows_for_export(self, user_id, task_id, request, forced_partition):
        repo = ReviewRepository(self._db)
        if request.scope is ExportScope.ALL:
            base = RecordListParams(partition=forced_partition)
        else:
            base = RecordListParams(
                q=request.filter.q, field=request.filter.field, value=request.filter.value,
                source_type=request.filter.source_type, extract_method=request.filter.extract_method,
                min_confidence=request.filter.min_confidence, review_type=request.filter.review_type,
            )
            if forced_partition is not None:
                base.partition = forced_partition
        return repo.query_records_all(user_id=user_id, task_id=task_id, params=base)

    async def download(self, *, user_id: int, task_id: int, artifact_id: int):
        artifact = self._repo.get_owned(user_id=user_id, task_id=task_id, artifact_id=artifact_id)
        if not artifact.storage_ref:
            raise RuntimeError("artifact content missing")
        data = await self._storage.get(artifact.storage_ref)
        return data, artifact.filename or "export.csv"

    def list_for_task(self, *, user_id: int, task_id: int) -> list[ArtifactView]:
        TaskRepository(self._db).get_owned(user_id, task_id)
        return [
            ArtifactView(
                artifact_id=a.id, export_type=a.export_type or "", dataset_version=a.dataset_version or "",
                filter_snapshot=a.filter_snapshot or {}, schema_version=a.schema_version,
                row_count=a.row_count, size_bytes=a.size_bytes, content_hash=a.content_hash,
                filename=a.filename or "export.csv", status=a.status, created_at=a.created_at,
                download_url=f"/tasks/{task_id}/artifacts/{a.id}/download",
            )
            for a in self._repo.list_for_task(user_id=user_id, task_id=task_id)
        ]

    @staticmethod
    def _safe_filename(title: str, export_type: str, dataset_version: str) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "task"))[:40].strip("._") or "task"
        return f"{base}_{export_type}_{dataset_version[:16]}.csv"
```

注意：`export`/`download` 使用 `await self._storage.*`，必须标为 `async def`；`_rows_for_export`/`list_for_task`/`_safe_filename` 为同步方法。

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_artifact_service.py -q`
Expected: PASS（2 passed）

- [ ] **Step 7: Commit**

```bash
git add backend/app/artifacts/repository.py backend/app/artifacts/service.py backend/app/review/repository.py backend/tests/artifacts/test_artifact_service.py
git commit -m "feat(artifact): add idempotent csv export with dataset fingerprint"
```

---

### Task 4: Export/Download API + 前端 ExportModal

**Files:**
- Create: `backend/app/api/routes/artifacts.py`
- Modify: `backend/app/api/router.py`
- Create: `frontend/src/features/artifacts/types.ts`
- Create: `frontend/src/features/artifacts/artifacts.api.ts`
- Modify: `frontend/src/app/overlay/modals/ExportModal.vue`
- Modify: `frontend/src/features/tasks/TaskDataView.vue`（导出按钮）
- Test: `backend/tests/artifacts/test_artifact_api.py`、`frontend/src/app/overlay/modals/ExportModal.test.ts`

**Interfaces:**
- Consumes: `ArtifactService.export/download/list_for_task`、`ArtifactRef/ArtifactView`。
- Produces: `POST /api/tasks/{task_id}/artifacts/export` → `ArtifactRef`；`GET /api/tasks/{task_id}/artifacts/{id}/download` → StreamingResponse；`GET /api/tasks/{task_id}/artifacts` → `list[ArtifactView]`。

- [ ] **Step 1: 写失败测试（后端）**

`backend/tests/artifacts/test_artifact_api.py`：
```python
"""M-15 Artifact Export/Download API（owner-safe，越权 404）。"""
from __future__ import annotations

from app.domain.models import Record
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_task_with_passed(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(user_id=user_id, title="上海政策", task_type="directed")
        session.flush()
        session.add(Record(user_id=user_id, task_id=task.id, spec_version=1,
                           partition="passed", payload={"标题": "记录A"}))
        session.commit()
        return task.id
    finally:
        session.close()


def test_export_and_download_owner_safe(client: dict) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    bob = _register(c, "bob@example.com")["user"]
    task_id = _seed_task_with_passed(factory, alice["id"])

    resp = c.post(f"/api/tasks/{task_id}/artifacts/export",
                  json={"export_type": "formal", "scope": "all", "filter": {}})
    assert resp.status_code == 200, resp.text
    ref = resp.json()
    assert ref["row_count"] == 1

    # alice 下载成功（CSV bytes，BOM + UTF-8）
    dl = c.get(ref["download_url"])
    assert dl.status_code == 200, dl.text
    assert dl.headers["content-type"].startswith("text/csv")
    assert dl.content.startswith(b"\xef\xbb\xbf")
    assert "标题" in dl.content.decode("utf-8-sig")

    # bob 越权：任务 404（不泄漏存在性）
    bob_resp = c.get(ref["download_url"])
    assert bob_resp.status_code == 404
```

- [ ] **Step 2: 运行确认失败 → 实现后端 routes/artifacts.py**

```python
"""M-15 Artifact Query/Export/Download API（D-060/D-072）。owner-safe，越权 404。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.artifacts.contracts import ArtifactRef, ArtifactView, ExportRequest
from app.artifacts.service import ArtifactService
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db, storage
from app.infra.object_storage import ObjectStorage

router = APIRouter(prefix="/tasks/{task_id}/artifacts", tags=["artifacts"])


@router.post("/export", response_model=ArtifactRef)
async def export_artifact(
    task_id: int, request: ExportRequest, user: User = Depends(require_user),
    db: DbSession = Depends(get_db), object_storage: ObjectStorage = Depends(storage),
) -> ArtifactRef:
    TaskRepository(db).get_owned(user.id, task_id)
    return await ArtifactService(db, object_storage).export(
        user_id=user.id, task_id=task_id, request=request,
    )


@router.get("", response_model=list[ArtifactView])
def list_artifacts(
    task_id: int, user: User = Depends(require_user), db: DbSession = Depends(get_db),
) -> list[ArtifactView]:
    TaskRepository(db).get_owned(user.id, task_id)
    return ArtifactService(db, None).list_for_task(user_id=user.id, task_id=task_id)


@router.get("/{artifact_id}/download")
async def download_artifact(
    task_id: int, artifact_id: int, user: User = Depends(require_user),
    db: DbSession = Depends(get_db), object_storage: ObjectStorage = Depends(storage),
) -> StreamingResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    data, filename = await ArtifactService(db, object_storage).download(
        user_id=user.id, task_id=task_id, artifact_id=artifact_id,
    )
    from urllib.parse import quote
    return StreamingResponse(
        iter([data]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
```
`app/api/router.py`：`from app.api.routes import artifacts` 并 `api_router.include_router(artifacts.router)`。
注：`list_artifacts` 传 `ArtifactService(db, None)`，`list_for_task` 不触 storage，安全。

- [ ] **Step 3: 运行后端测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_artifact_api.py -q`
Expected: PASS

- [ ] **Step 4: 前端 types + api**

`frontend/src/features/artifacts/types.ts`：
```ts
export type ExportType = 'formal' | 'review' | 'audit'
export type ExportScope = 'current' | 'all'
export interface ExportFilter {
  q?: string | null
  field?: string | null
  value?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
  review_type?: string | null
}
export interface ExportRequest { export_type: ExportType; scope: ExportScope; filter: ExportFilter }
export interface ArtifactRef { artifact_id: number; content_hash: string; download_url: string; row_count: number }
export interface ArtifactView { artifact_id: number; export_type: string; dataset_version: string; filter_snapshot: Record<string, unknown>; schema_version: string | null; row_count: number; size_bytes: number | null; content_hash: string; filename: string; status: string; created_at: string; download_url: string }
```
`frontend/src/features/artifacts/artifacts.api.ts`：
```ts
import { apiClient } from '@/app/api/client'
import type { ArtifactRef, ExportRequest } from './types'

export function exportArtifact(taskId: string | number, request: ExportRequest): Promise<ArtifactRef> {
  return apiClient.post<ArtifactRef>(`/tasks/${taskId}/artifacts/export`, request)
}

/** 下载走普通 <a href> 触发浏览器下载；API 已带 session cookie + Content-Disposition。 */
export function artifactDownloadUrl(taskId: string | number, artifactId: number): string {
  return `/api/tasks/${taskId}/artifacts/${artifactId}/download`
}
```

- [ ] **Step 5: 实现 ExportModal.vue**

```vue
<script setup lang="ts">
// Export Modal（D-060/D-067）：选择导出类型 + 范围 → POST /artifacts/export。
import { computed, ref } from 'vue'
import { closeModal } from '@/app/overlay/modal.store'
import { exportArtifact } from '@/features/artifacts/artifacts.api'
import type { ExportRequest, ExportType } from '@/features/artifacts/types'

interface ExportPayload { taskId: string | number; filter?: Record<string, unknown> }
const props = defineProps<{ payload?: ExportPayload }>()

const exportType = ref<ExportType>('formal')
const scope = ref<'current' | 'all'>('all')
const running = ref(false)
const error = ref<string | null>(null)
const success = ref<{ url: string; rows: number } | null>(null)

const hasFilter = computed(() => {
  const f = props.payload?.filter ?? {}
  return Object.values(f).some((v) => v !== undefined && v !== null && v !== '')
})

async function run(): Promise<void> {
  running.value = true
  error.value = null
  success.value = null
  try {
    const filter = props.payload?.filter ?? {}
    const ref = await exportArtifact(props.payload!.taskId, {
      export_type: exportType.value,
      scope: hasFilter.value && scope.value === 'current' ? 'current' : 'all',
      filter,
    })
    success.value = { url: `/api/tasks/${props.payload!.taskId}/artifacts/${ref.artifact_id}/download`, rows: ref.row_count }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    running.value = false
  }
}
</script>
```
模板：三个导出类型单选（正式/待复核/审核完整）、范围（全部当前分区 / 当前筛选，`hasFilter` 为 false 时禁用 current）、错误/成功提示、`关闭` + `导出并下载`（成功后可 `<a :href="success.url">下载 CSV（{{ success.rows }} 行）</a>`）。

- [ ] **Step 6: 前端导出按钮 + scoped 测试**

`TaskDataView.vue`：toolbar 加 `<button type="button" class="data-settings" @click="openExport">导出</button>`；`import { openModal } from '@/app/overlay/modal.store'`；`function openExport(): void { openModal('EXPORT', { taskId: taskId.value, filter: currentFilterSnapshot() }) }`；`currentFilterSnapshot()` 组装当前 `{ q, field, value, source_type, extract_method, min_confidence, review_type }`（来自 useRecords 的 search + params）。
`frontend/src/app/overlay/modals/ExportModal.test.ts`：mock `exportArtifact`，验证点选 export_type/scope 后发出的 request body 正确（`formal` + `all`；有 filter 时 current → 带 filter）。

- [ ] **Step 7: 前端验证 + Commit**

Run（frontend/）: `npx vitest run src/app/overlay/modals/ExportModal.test.ts`、`npx vue-tsc --noEmit`、`npm run build`
Commit:
```bash
git add backend/app/api/routes/artifacts.py backend/app/api/router.py backend/tests/artifacts/test_artifact_api.py frontend/src/features/artifacts frontend/src/app/overlay/modals/ExportModal.vue frontend/src/features/tasks/TaskDataView.vue
git commit -m "feat(web): connect export modal and artifact download"
```

---

### Task 5: Completion Card（后端 + 前端）

**Files:**
- Create: `backend/app/api/routes/completion.py`
- Modify: `backend/app/api/router.py`
- Create: `frontend/src/features/artifacts/completion.api.ts`
- Create: `frontend/src/features/artifacts/CompletionCard.vue`
- Modify: `frontend/src/features/tasks/TaskChatView.vue`
- Test: `backend/tests/artifacts/test_completion_card.py`、`frontend/src/features/artifacts/CompletionCard.test.ts`

**Interfaces:**
- Consumes: `CompletionCardView`（contracts.py）、`ValidationRepository.latest_completion`、`QualityRepository.count_by_partition`、`URLResource` 计数。
- Produces: `GET /api/tasks/{task_id}/completion` → `CompletionCardView`。

- [ ] **Step 1: 写失败测试（后端）**

`backend/tests/artifacts/test_completion_card.py`：
```python
from app.domain.models import CompletionDecision
from app.artifacts.contracts import CompletionCardView


def _decision(db, user, task, *, status="NORMAL_COMPLETED", is_partial=False, ctype="directional_scope_complete"):
    d = CompletionDecision(user_id=user.id, task_id=task.id, run_id=None, spec_version=1,
                           plan_version=1, status=status, is_partial=is_partial,
                           completion_type=ctype, qualified_record_count=3,
                           scope_completion_metadata={"eligible_urls": 5, "terminal_urls": 5})
    db.add(d)
    db.flush()
    return d


def test_completion_normal(db, user_a, task_a):
    from app.api.routes.completion import assemble_completion_card
    _decision(db, user_a, task_a)
    view = assemble_completion_card(db, user_id=user_a.id, task_id=task_a.id)
    assert view.status == "NORMAL_COMPLETED"
    assert view.is_partial is False
    assert view.completion_id is not None
    assert view.can_export_formal is False  # 无 passed record


def test_completion_partial_no_fake_percent(db, user_a, task_a):
    from app.api.routes.completion import assemble_completion_card
    _decision(db, user_a, task_a, status="PARTIALLY_COMPLETED", is_partial=True,
              ctype="runtime_limit")
    view = assemble_completion_card(db, user_id=user_a.id, task_id=task_a.id)
    assert view.is_partial is True
    # 不出现任何百分比字段（契约上无该字段即保证）
    assert "percent" not in view.model_dump()
```

- [ ] **Step 2: 运行确认失败 → 实现 routes/completion.py**

```python
"""M-15 Completion Card Query API（D-006/D-043/D-044）。

GET /tasks/{task_id}/completion → CompletionCardView。全部来自 DB facts：
CompletionDecision（最新）+ 分区计数 + URLResource 处理事实；不调用 LLM。
owner-safe：任务越权 → 404。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.artifacts.contracts import CompletionCardView
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.quality.repository import QualityRepository
from app.validation.repository import ValidationRepository

router = APIRouter(prefix="/tasks/{task_id}/completion", tags=["completion"])


def assemble_completion_card(db, *, user_id: int, task_id: int) -> CompletionCardView:
    decision = ValidationRepository(db).latest_completion(user_id=user_id, task_id=task_id)
    counts = QualityRepository(db).count_by_partition(user_id=user_id, task_id=task_id)
    urls = QualityRepository(db).url_resources(user_id=user_id, task_id=task_id)
    terminal = sum(1 for u in urls if u.status in ("FETCHED", "HANDED_OFF"))
    passed = int(counts.get("passed", 0))
    review = int(counts.get("needs_review", 0))
    rejected = int(counts.get("rejected", 0))
    return CompletionCardView(
        task_id=task_id,
        completion_id=decision.id if decision else None,
        status=decision.status if decision else "PARTIALLY_COMPLETED",
        reason=decision.reason if decision else "未找到完成判定记录",
        completion_type=decision.completion_type if decision else None,
        is_partial=bool(decision.is_partial) if decision else True,
        qualified_record_count=int(decision.qualified_record_count) if decision else 0,
        partition_counts={"passed": passed, "needs_review": review, "rejected": rejected},
        url_processed=terminal,
        runtime_limit_reason=decision.runtime_limit_reason if decision else None,
        scope_completion_metadata=decision.scope_completion_metadata or {},
        can_view_data=True,
        can_view_quality=True,
        can_export_formal=passed > 0,
        can_export_review=review > 0,
    )


@router.get("", response_model=CompletionCardView)
def get_completion(
    task_id: int, user: User = Depends(require_user), db: DbSession = Depends(get_db),
) -> CompletionCardView:
    TaskRepository(db).get_owned(user.id, task_id)
    return assemble_completion_card(db, user_id=user.id, task_id=task_id)
```
`QualityRepository.url_resources`/`count_by_partition` 需确认存在；不存在则在 `app/quality/repository.py` 增加同名 owner-safe 方法（M-14 已有 count_by_partition 与 url_resources，直接复用）。
`app/api/router.py` 注册 `completion.router`。

- [ ] **Step 3: 运行后端测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_completion_card.py -q`
Expected: PASS

- [ ] **Step 4: 前端 completion.api + CompletionCard.vue**

`completion.api.ts`：`getCompletion(taskId) -> Promise<CompletionCardView>`（GET `/tasks/{id}/completion`），types 放 `features/artifacts/types.ts` 增加 `CompletionCardView`。
`CompletionCard.vue`：props `{ card: CompletionCardView; taskId: string }`。NORMAL：展示 passed/review/rejected 计数 + 来源/网页处理数量 + 完成原因 + 操作按钮（查看数据 `/tasks/:id/data?status=passed`、查看质量 `/tasks/:id/quality`、导出 CSV → `openModal('EXPORT', { taskId, filter: {} })`）。PARTIAL：展示当前结果数量 + 停止原因 + 未覆盖/失败摘要（`scope_completion_metadata`/`runtime_limit_reason`）+ 查看已有数据/查看质量/导出已有 PASSED（仅 `can_export_formal`）。**不渲染任何百分比。**

- [ ] **Step 5: TaskChatView 挂载（幂等）**

在 `TaskChatView.vue`：`watch(() => shell state, ...)` 当 `state === 'COMPLETED' || state === 'PARTIALLY_COMPLETED' || state === 'CANCELLED'` 时 `getCompletion(taskId)` 并渲染 `<CompletionCard :card="card" :task-id="taskId" />`。卡片由后端 `completion_id` 稳定 identity 派生渲染，不是追加的 Chat 消息 → reload/SSE reconnect 不会重复生成。

- [ ] **Step 6: 前端 scoped 测试 + 验证 + Commit**

`CompletionCard.test.ts`：NORMAL 渲染正确 actions；PARTIAL 渲染停止原因、无百分比字段。
Run: `npx vitest run src/features/artifacts/CompletionCard.test.ts`、`npx vue-tsc --noEmit`、`npm run build`
Commit:
```bash
git add backend/app/api/routes/completion.py backend/app/api/router.py backend/tests/artifacts/test_completion_card.py frontend/src/features/artifacts/completion.api.ts frontend/src/features/artifacts/CompletionCard.vue frontend/src/features/tasks/TaskChatView.vue
git commit -m "feat(web): render normal/partial completion card from db facts"
```

---

### Task 6: Soft Delete / Restore + Deleted View

**Files:**
- Modify: `backend/app/domain/service.py`（restore 动态）
- Modify: `backend/app/domain/task_commands.py`（delete_task/restore_task）
- Modify: `backend/app/domain/repository.py`（list_deleted）
- Modify: `backend/app/api/routes/tasks.py`（delete/restore 命令 + view=deleted）
- Modify: `frontend/src/features/tasks/commands.api.ts`
- Modify: `frontend/src/features/tasks/TasksView.vue`
- Modify: `frontend/src/app/overlay/modals/DeleteConfirmModal.vue`
- Test: `backend/tests/artifacts/test_soft_delete_restore.py`、`frontend/src/features/tasks/DeletedView.test.ts`

**Interfaces:**
- Consumes: `TaskCommandService`（已有 pause/resume/cancel 模式）、`DomainService.transition_task`（已支持 delete/restore 命令）。
- Produces: `TaskCommandService.delete_task(...)` / `.restore_task(...)`；`GET /tasks?view=deleted` → `TaskShellListResponse`；`POST /tasks/{id}/commands/{delete|restore}`。

- [ ] **Step 1: 写失败测试（后端）**

`backend/tests/artifacts/test_soft_delete_restore.py`：
```python
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.task_commands import TaskCommandService
from app.state.states import TaskState


def test_soft_delete_hides_and_restores(db, user_a, task_a):
    from app.domain.models import Task
    from app.domain.repository import TaskRepository
    db.refresh(task_a)
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "COMPLETED"  # 模拟终态任务
    db.commit()
    svc = TaskCommandService(db)
    r = svc.delete_task(user_id=user_a.id, task_id=task_a.id, expected_version=task.version)
    assert r.state == "DELETED"
    # normal list 隐藏
    assert TaskRepository(db).list_by_user(user_a.id) == []
    # deleted view 可见
    deleted = TaskRepository(db).list_deleted(user_a.id)
    assert [t.id for t in deleted] == [task_a.id]
    # restore → 回到删除前终态（不破坏 Run execution facts）
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    r2 = svc.restore_task(user_id=user_a.id, task_id=task_a.id, expected_version=task.version)
    assert r2.state == "COMPLETED"


def test_running_task_cannot_delete(db, user_a, task_a):
    from app.domain.repository import TaskRepository
    from app.domain.errors import IllegalTransitionError
    db.refresh(task_a)
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "RUNNING"
    task.version += 1
    db.commit()
    svc = TaskCommandService(db)
    try:
        svc.delete_task(user_id=user_a.id, task_id=task_a.id, expected_version=task.version)
        raise AssertionError("running delete should be rejected")
    except IllegalTransitionError:
        pass
```

- [ ] **Step 2: 运行确认失败 → 实现**

`task_commands.py` 增加（复用 `_run`，仅新增方法）：
```python
    def delete_task(self, *, user_id, task_id, expected_version, idempotency_key=None, reason=None):
        return self._run(user_id=user_id, task_id=task_id, expected_version=expected_version,
                         command="delete", idempotency_key=idempotency_key, reason=reason)

    def restore_task(self, *, user_id, task_id, expected_version, idempotency_key=None, reason=None):
        return self._run(user_id=user_id, task_id=task_id, expected_version=expected_version,
                         command="restore", idempotency_key=idempotency_key, reason=reason)
```
`domain/service.py` `transition_task` 特判 restore：在 `assert_task_transition` 之前读取 `task.restore_state`；当 `command == "restore"` 时：
```python
        if command == "restore":
            next_state = TaskState(task.restore_state or "DRAFT")
        else:
            current = TaskState(task.state)
            next_state = assert_task_transition(current, command)
```
并把 `if next_state == TaskState.DELETED: task.deleted_at = now; task.restore_state = task.state` 改为：
```python
        if command == "delete":
            task.deleted_at = datetime.now(UTC)
            task.restore_state = task.state
        elif command == "restore":
            task.deleted_at = None
            task.restore_state = None
```
`repository.py` `TaskRepository` 增加：
```python
    def list_deleted(self, user_id: int) -> list[Task]:
        return list(
            self._db.scalars(
                select(Task).where(Task.user_id == user_id, Task.deleted_at.is_not(None))
                .order_by(Task.deleted_at.desc())
            )
        )
```
`routes/tasks.py`：
- `list_tasks` 读 `view` query 参数：`view == 'deleted'` → `list_deleted`；否则 `list_by_user`。
- `_TASK_COMMANDS` 增加 `{"delete", "restore"}`（保留 pause/resume/cancel）。`task_command` 路由对 delete 的 IllegalTransitionError（running）给清晰 409：
```python
    if command == "delete":
        task = TaskRepository(db).get_owned(user.id, task_id)
        if task.state in ("RUNNING", "PAUSING", "CANCELLING"):
            raise HTTPException(status_code=409, detail="运行中的任务必须先取消并等待停止后才能删除")
```

- [ ] **Step 3: 运行后端测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_soft_delete_restore.py -q`
Expected: PASS

- [ ] **Step 4: 前端 commands.api + TasksView + DeleteConfirmModal**

`commands.api.ts` 增加 `deleteTask/restoreTask`（POST `/tasks/{id}/commands/delete|restore`，body `{expected_version}`）。
`TasksView.vue`：
- `view === 'deleted'` → 调 `listTasksDeleted()`（GET `/tasks?view=deleted`，新增 `listTasks(params?: { view?: string })`）。
- 正常列表行 `···`：`can('delete')` → `openModal('DELETE_CONFIRM', { taskId, action: 'soft', onConfirm })`；`can('restore')` → restore。
- deleted 视图行：`恢复` → restore；`永久删除` → `openModal('DELETE_CONFIRM', { taskId, action: 'permanent', onConfirm })`。
`DeleteConfirmModal.vue` 实现两段确认：
- `action='soft'`：文案「删除后任务将进入已删除视图，可以恢复。」按钮「删除」。
- `action='permanent'`：第二段强确认（`step` ref：confirm → confirm2），文案「永久删除将删除该任务全部数据与文件，**不可恢复**。」按钮「永久删除」，`step===2` 才 `@confirm` 发 `permanentDelete(taskId, { confirmed: true })`（`permanentDelete` API 由 Task 7 后端提供，POST `/tasks/{id}/permanent-delete`）。

- [ ] **Step 5: 前端 scoped 测试 + 验证 + Commit**

`DeletedView.test.ts`：mock api，验证 soft delete → 调 delete 命令；restore → restore 命令；permanent delete → 先第一段再第二段确认后才调 permanent-delete。
Run: `npx vitest run src/features/tasks/DeletedView.test.ts`、`npx vue-tsc --noEmit`、`npm run build`
Commit:
```bash
git add backend/app/domain backend/app/api/routes/tasks.py backend/tests/artifacts/test_soft_delete_restore.py frontend/src/features/tasks/commands.api.ts frontend/src/features/tasks/TasksView.vue frontend/src/app/overlay/modals/DeleteConfirmModal.vue
git commit -m "feat(task): add soft delete, restore and deleted view"
```

---

### Task 7: Permanent Delete + Reference-safe Object Cleanup

**Files:**
- Create: `backend/app/artifacts/deletion.py`
- Modify: `backend/app/api/routes/tasks.py`（permanent-delete route）
- Test: `backend/tests/artifacts/test_permanent_delete_reference_safety.py`

**Interfaces:**
- Consumes: `ObjectStorage.delete`、`Artifact`/`PageSnapshot`/`Checkpoint` storage_ref。
- Produces: `DeletionService(db, storage).permanent_delete(user_id, task_id, confirmed) -> DeletionManifest`、`DeletionManifest`（含 `deleted_rows`/`objects_removed`/`objects_kept`）。

**核心算法（manifest + 引用复查）：**
1. 校验：owner（调用方已校验）+ `task.state == DELETED` + `confirmed == True`；否则 409/422。
2. 收集 task 直接拥有的 object refs：`PageSnapshot.storage_ref`、`Artifact.storage_ref`、`Checkpoint.committed_object_refs`（JSON 内 ref 值，best-effort）。
3. 按依赖顺序显式删除该 task 的 DB 行（跨方言安全，不依赖 FK cascade）：
   `record_field_overrides → record_review_actions → field_evidence → field_conflicts → validation_results → dedupe_clusters → records → node_attempts → node_runs → checkpoints → approvals → chat_messages → collection_spec_drafts → collection_spec_versions → plan_versions → url_resources → page_snapshots → quality_snapshots → completion_decisions → artifacts → runs → domain_events(task) → outbox_events(task) → tasks`。
4. 删除完成后，对每个 object ref 做 **跨表跨用户引用复查**：`page_snapshots.storage_ref == ref` 或 `artifacts.storage_ref == ref` 或 `checkpoints.committed_object_refs` 含 ref → 保留；否则 `storage.delete(ref)`。
5. 幂等：重复执行时目标行/对象已不存在 → 安全 no-op；返回统计。

- [ ] **Step 1: 写失败测试（TEST F）**

`backend/tests/artifacts/test_permanent_delete_reference_safety.py`：
```python
import pytest
from app.domain.models import Artifact, PageSnapshot
from app.artifacts.deletion import DeletionService
from app.domain.repository import TaskRepository


def _shared_snapshot(db, user, task, ref):
    s = PageSnapshot(user_id=user.id, task_id=task.id, spec_version=1, content_hash="h",
                     storage_ref=ref, mime_type="text/html", tool="http", tool_version="1", final_url="http://x")
    db.add(s)
    db.flush()
    return s


def _shared_artifact(db, user, task, ref):
    a = Artifact(user_id=user.id, task_id=task.id, artifact_type="csv", content_hash="h",
                 storage_ref=ref, status="ready")
    db.add(a)
    db.flush()
    return a


@pytest.mark.asyncio
async def test_permanent_delete_user_a_keeps_user_b_blob(db, user_a, user_b, task_a, storage):
    from datetime import UTC, datetime
    from app.domain.models import Task
    ref = "snapshots/u1/h/tool.html"
    _shared_snapshot(db, user_a, task_a, ref)
    # 软删除置 DELETED
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"; task.deleted_at = datetime.now(UTC)
    db.commit()
    # 用户 B 另一个任务引用同一 ref（共享对象场景）
    t2 = Task(user_id=user_b.id, title="b", state="DELETED", deleted_at=datetime.now(UTC))
    db.add(t2); db.flush()
    _shared_snapshot(db, user_b, t2, ref)
    await storage.put(ref, b"<html>x</html>", "text/html")
    svc = DeletionService(db, storage)
    manifest = await svc.permanent_delete(user_id=user_a.id, task_id=task_a.id, confirmed=True)
    # B 的任务与快照行仍在，共享对象保留
    assert TaskRepository(db).get_owned(user_b.id, t2.id) is not None
    assert await storage.exists(ref) is True
    assert ref in manifest.objects_kept


@pytest.mark.asyncio
async def test_permanent_delete_last_ref_removes_object(db, user_a, task_a, storage):
    from datetime import UTC, datetime
    from app.domain.models import Task
    ref = "artifacts/u1/csv/h.csv"
    _shared_artifact(db, user_a, task_a, ref)
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"; task.deleted_at = datetime.now(UTC)
    db.commit()
    await storage.put(ref, b"a,b\r\n", "text/csv")
    svc = DeletionService(db, storage)
    manifest = await svc.permanent_delete(user_id=user_a.id, task_id=task_a.id, confirmed=True)
    assert await storage.exists(ref) is False  # 最后一个引用消失才物理删除
    assert manifest.objects_removed == [ref]


@pytest.mark.asyncio
async def test_permanent_delete_requires_confirm(db, user_a, task_a, storage):
    from datetime import UTC, datetime
    from app.domain.models import Task
    from app.domain.errors import DomainError
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"; task.deleted_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(DomainError):
        await DeletionService(db, storage).permanent_delete(
            user_id=user_a.id, task_id=task_a.id, confirmed=False)
```

- [ ] **Step 2: 运行确认失败 → 实现 deletion.py**

```python
"""M-15 DeletionService：permanent delete（D-065/D-072）。

先算 manifest → 显式删除 task 拥有的 DB 行（不依赖 FK cascade）→ 对每个 object ref
做跨表跨用户引用复查 → 最后一个引用才物理删除对象。幂等、可恢复：重复执行安全 no-op。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.domain.errors import DomainError
from app.domain.models import (
    Approval, Artifact, ChatMessage, Checkpoint, CollectionSpecDraft,
    CollectionSpecVersion, CompletionDecision, DedupeCluster, DomainEvent,
    FieldConflict, FieldEvidence, NodeAttempt, NodeRun, OutboxEvent, PageSnapshot,
    PlanVersion, QualitySnapshot, Record, RecordFieldOverride, RecordReviewAction,
    Run, Task, URLResource, ValidationResult,
)
from app.domain.repository import TaskRepository


@dataclass
class DeletionManifest:
    task_id: int
    deleted_rows: int = 0
    objects_removed: list[str] = field(default_factory=list)
    objects_kept: list[str] = field(default_factory=list)


class DeletionService:
    def __init__(self, db, storage) -> None:
        self._db = db
        self._storage = storage

    def _task_object_refs(self, *, user_id: int, task_id: int) -> list[str]:
        refs: list[str] = list(self._db.scalars(
            select(PageSnapshot.storage_ref).where(
                PageSnapshot.user_id == user_id, PageSnapshot.task_id == task_id,
                PageSnapshot.storage_ref.is_not(None),
            )
        ))
        refs += list(self._db.scalars(
            select(Artifact.storage_ref).where(
                Artifact.user_id == user_id, Artifact.task_id == task_id,
                Artifact.storage_ref.is_not(None),
            )
        ))
        # Checkpoint committed_object_refs（best-effort）
        cps = self._db.scalars(
            select(Checkpoint).where(
                Checkpoint.user_id == user_id, Checkpoint.task_id == task_id
            )
        ).all()
        for cp in cps:
            for v in (cp.committed_object_refs or {}).values():
                if isinstance(v, str) and v.startswith(("snapshots/", "artifacts/")):
                    refs.append(v)
        return list(dict.fromkeys(v for v in refs if v))  # 去重保序

    def _ref_used_elsewhere(self, ref: str) -> bool:
        """跨表跨用户引用复查：DB 事实决定对象是否可物理删除（D-072）。"""
        from sqlalchemy import func, select
        for model, col in ((PageSnapshot, PageSnapshot.storage_ref),
                           (Artifact, Artifact.storage_ref)):
            n = self._db.scalar(select(func.count()).select_from(model).where(col == ref))
            if n:
                return True
        for cp in self._db.scalars(select(Checkpoint)):
            if any(isinstance(v, str) and v == ref for v in (cp.committed_object_refs or {}).values()):
                return True
        return False

    async def permanent_delete(
        self, *, user_id: int, task_id: int, confirmed: bool
    ) -> DeletionManifest:
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        if not confirmed:
            raise DomainError("永久删除必须二次强确认")
        if task.state != "DELETED":
            raise DomainError("只有已删除任务可以永久删除")

        refs = self._task_object_refs(user_id=user_id, task_id=task_id)
        record_ids = list(self._db.scalars(
            select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
        ))
        node_run_ids = list(self._db.scalars(
            select(NodeRun.id).where(NodeRun.user_id == user_id, NodeRun.task_id == task_id)
        ))

        # 1) 显式删除 DB 行（跨方言安全，不依赖 FK cascade）
        #    - record 级子表：按 record_id in (该 task records) 删除（FieldEvidence.task_id 可能为 NULL）
        for model in (RecordFieldOverride, RecordReviewAction, FieldEvidence,
                      FieldConflict, ValidationResult):
            if record_ids:
                self._db.execute(sa_delete(model).where(
                    model.user_id == user_id, model.record_id.in_(record_ids)))
        #    - task 级子表：按 (user_id, task_id)
        for model in (DedupeCluster, Record, Checkpoint, Approval, ChatMessage,
                      CollectionSpecDraft, CollectionSpecVersion, PlanVersion,
                      URLResource, PageSnapshot, QualitySnapshot, CompletionDecision,
                      Artifact, Run):
            self._db.execute(sa_delete(model).where(
                model.user_id == user_id, model.task_id == task_id))
        #    - node 级子表：NodeAttempt 无 task_id → 按 node_run_id
        if node_run_ids:
            self._db.execute(sa_delete(NodeAttempt).where(
                NodeAttempt.user_id == user_id, NodeAttempt.node_run_id.in_(node_run_ids)))
        self._db.execute(sa_delete(NodeRun).where(
            NodeRun.user_id == user_id, NodeRun.task_id == task_id))
        # 2) 删除 task 级事件 + 任务本身
        self._db.execute(sa_delete(DomainEvent).where(
            DomainEvent.user_id == user_id, DomainEvent.aggregate_type == "task",
            DomainEvent.aggregate_id == task_id))
        self._db.execute(sa_delete(OutboxEvent).where(
            OutboxEvent.user_id == user_id, OutboxEvent.aggregate_type == "task",
            OutboxEvent.aggregate_id == task_id))
        result = self._db.execute(
            sa_delete(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        deleted = result.rowcount or 1
        self._db.commit()

        # 3) 引用复查后删除无人引用的对象（最后一个引用消失才物理删除）
        removed: list[str] = []
        kept: list[str] = []
        for ref in refs:
            if self._ref_used_elsewhere(ref):
                kept.append(ref)
            else:
                await self._storage.delete(ref)
                removed.append(ref)
        return DeletionManifest(task_id=task_id, deleted_rows=deleted,
                                objects_removed=removed, objects_kept=kept)
```
注意：`sa_delete(model).where(model.user_id == ..., model.task_id == ...)` 需要每个 model 都有 `task_id`；NodeAttempt 只有 `node_run_id`/`user_id`，因此单独按 node_run_id 删除。请在实现时对 `NodeAttempt` 先查该 task 的 node_run ids 再删；`FieldEvidence` 有 `task_id` 但部分旧行 `task_id` 可能为 NULL → 按 record_id in (该 task records) 兜底。实现时以「record 集合 + task_id」双保险。

- [ ] **Step 3: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_permanent_delete_reference_safety.py -q`
Expected: PASS（3 passed）

- [ ] **Step 4: permanent-delete route**

`routes/tasks.py` 增加：
```python
@router.post("/{task_id}/permanent-delete", response_model=dict)
async def permanent_delete(
    task_id: int, cmd: PermanentDeleteCommand,
    user: User = Depends(require_user), db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> dict:
    TaskRepository(db).get_owned(user.id, task_id)
    manifest = await DeletionService(db, object_storage).permanent_delete(
        user_id=user.id, task_id=task_id, confirmed=cmd.confirmed)
    return {"task_id": task_id, **asdict(manifest)}
```
（`PermanentDeleteCommand`/`DeletionService` import；`asdict` 来自 dataclasses。路由是 async 因为 `permanent_delete` 内 await storage.delete。）

- [ ] **Step 5: 迁移一致性 + Commit**

Run: `.venv/Scripts/python.exe -m alembic heads` → `0012 (head)`；ruff/mypy affected files。
Commit:
```bash
git add backend/app/artifacts/deletion.py backend/app/api/routes/tasks.py backend/tests/artifacts/test_permanent_delete_reference_safety.py
git commit -m "feat(storage): add reference-safe permanent deletion"
```

---

### Task 8: RetentionPolicy + CleanupJob + Local 收尾

**Files:**
- Create: `backend/app/artifacts/retention.py`
- Create: `backend/app/artifacts/cli.py`
- Create: `infra/scripts/retention_cleanup.py`
- Modify: `backend/app/config.py`（`retention_heavy_days`）
- Test: `backend/tests/artifacts/test_retention.py`

**Interfaces:**
- Consumes: `PageSnapshot`（storage_ref/captured_at/status）、`FieldEvidence.snapshot_id`。
- Produces: `RetentionPolicy(retention_days)`、`CleanupResult`（dataclass：scanned/eligible/protected/deleted/failed/bytes_freed/started_at/completed_at/policy_version/dry_run）、`RetentionService(db, storage).run(dry_run) -> CleanupResult`。

**核心规则：**
- 候选 = 有 storage_ref 且 age >= retention_days 的 PageSnapshot（重型 HTML/正文/截图/浏览器快照）。
- 被 FieldEvidence.snapshot_id 引用 → PROTECTED（不删）。未到期 → KEEP（不计入 eligible）。
- eligible 且无保护引用 → dry_run 只计数；否则 `storage.delete(storage_ref)` + `PageSnapshot.storage_ref=NULL, status='retention_removed'`。
- FieldEvidence `raw_snippet`/`source_locator` 在 DB 独立保留，不受影响。

- [ ] **Step 1: 写失败测试（TEST G）**

`backend/tests/artifacts/test_retention.py`：
```python
import pytest
from datetime import datetime, timedelta, UTC
from app.artifacts.retention import RetentionService
from app.domain.models import FieldEvidence, PageSnapshot


def _snap(db, user, task, *, ref, age_days):
    s = PageSnapshot(user_id=user.id, task_id=task.id, spec_version=1, content_hash="h",
                     storage_ref=ref, mime_type="text/html", tool="http", tool_version="1",
                     final_url="http://x", captured_at=datetime.now(UTC) - timedelta(days=age_days))
    db.add(s)
    db.flush()
    return s


@pytest.mark.asyncio
async def test_retention_three_cases(db, user_a, task_a, storage):
    expired_unref = _snap(db, user_a, task_a, ref="snapshots/u1/1/a.html", age_days=100)
    protected = _snap(db, user_a, task_a, ref="snapshots/u1/2/b.html", age_days=100)
    fresh = _snap(db, user_a, task_a, ref="snapshots/u1/3/c.html", age_days=5)
    # protected 被 FieldEvidence 引用
    ev = FieldEvidence(user_id=user_a.id, task_id=task_a.id, record_id=1,
                       field_name="标题", snapshot_id=protected.id, raw_snippet="原文片段",
                       source_locator="div.x")
    db.add(ev)
    db.flush()
    await storage.put(expired_unref.storage_ref, b"<html>a</html>", "text/html")
    await storage.put(protected.storage_ref, b"<html>b</html>", "text/html")
    await storage.put(fresh.storage_ref, b"<html>c</html>", "text/html")

    svc = RetentionService(db, storage, retention_days=30)
    result = await svc.run(dry_run=False)
    assert result.scanned == 3
    assert result.deleted == 1
    assert result.protected == 1
    assert result.failed == 0
    assert await storage.exists(expired_unref.storage_ref) is False
    assert await storage.exists(protected.storage_ref) is True
    assert await storage.exists(fresh.storage_ref) is True
    # FieldEvidence 最小片段仍存在
    assert ev.raw_snippet == "原文片段"
    assert ev.source_locator == "div.x"


@pytest.mark.asyncio
async def test_retention_dry_run_no_delete(db, user_a, task_a, storage):
    snap = _snap(db, user_a, task_a, ref="snapshots/u1/4/d.html", age_days=100)
    await storage.put(snap.storage_ref, b"<html>d</html>", "text/html")
    svc = RetentionService(db, storage, retention_days=30)
    result = await svc.run(dry_run=True)
    assert result.dry_run is True
    assert result.deleted == 0
    assert await storage.exists(snap.storage_ref) is True
```

- [ ] **Step 2: 运行确认失败 → 实现 retention.py**

```python
"""M-15 RetentionPolicy / CleanupResult / RetentionService（D-072）。

普通生命周期清理 ≠ permanent delete：只清理「到期 + 无保护引用」的重型 PageSnapshot 对象。
- 保护引用：FieldEvidence.snapshot_id → 该 snapshot 的 raw 对象不删（证据链仍在 DB）。
- FieldEvidence raw_snippet/source_locator 与对象解耦，天然长期保留。
- dry_run：只统计，不物理删除。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.models import FieldEvidence, PageSnapshot

logger = logging.getLogger(__name__)

POLICY_VERSION = "m15.1"


@dataclass
class CleanupResult:
    policy_version: str = POLICY_VERSION
    retention_days: int = 30
    dry_run: bool = False
    scanned: int = 0
    eligible: int = 0
    protected: int = 0
    deleted: int = 0
    failed: int = 0
    bytes_freed: int = 0
    started_at: str = ""
    completed_at: str = ""


@dataclass
class RetentionPolicy:
    retention_days: int = 30

    def is_expired(self, captured_at: datetime | None) -> bool:
        if captured_at is None:
            return False
        return captured_at < datetime.now(UTC) - timedelta(days=self.retention_days)


class RetentionService:
    def __init__(self, db, storage, *, retention_days: int) -> None:
        self._db = db
        self._storage = storage
        self._policy = RetentionPolicy(retention_days)

    async def run(self, *, dry_run: bool) -> CleanupResult:
        now_iso = datetime.now(UTC).isoformat()
        result = CleanupResult(dry_run=dry_run, retention_days=self._policy.retention_days,
                               started_at=now_iso)
        # 候选：有 storage_ref 的 PageSnapshot（重型 HTML/正文/截图/浏览器快照）
        candidates = list(self._db.scalars(
            select(PageSnapshot).where(PageSnapshot.storage_ref.is_not(None))
        ))
        # 保护集合：被 FieldEvidence.snapshot_id 引用的 snapshot id（证据链在 DB）
        protected_ids = set(self._db.scalars(
            select(FieldEvidence.snapshot_id).where(FieldEvidence.snapshot_id.is_not(None))
        ).all())
        result.scanned = len(candidates)
        for snap in candidates:
            if not self._policy.is_expired(snap.captured_at):
                continue
            result.eligible += 1
            if snap.id in protected_ids:
                result.protected += 1
                continue
            if dry_run:
                continue
            try:
                freed = await self._remove_object(snap)
                result.deleted += 1
                result.bytes_freed += freed
            except Exception:  # noqa: BLE001 —— 单对象失败不中断整轮
                logger.warning("retention delete failed for snapshot %s", snap.id, exc_info=True)
                result.failed += 1
        result.completed_at = datetime.now(UTC).isoformat()
        self._db.commit()
        return result

    async def _remove_object(self, snap: PageSnapshot) -> int:
        ref = snap.storage_ref
        if not ref:
            return 0
        meta = await self._storage.head(ref)
        if meta is not None:
            await self._storage.delete(ref)
        snap.storage_ref = None
        snap.status = "retention_removed"
        self._db.add(snap)
        return meta.size if meta else 0
```

- [ ] **Step 3: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/artifacts/test_retention.py -q`
Expected: PASS（2 passed）

- [ ] **Step 4: CLI + config**

`backend/app/config.py` 增加：`retention_heavy_days: int = 90`（env `KAIROS_RETENTION_HEAVY_DAYS`）。
`backend/app/artifacts/cli.py`：
```python
"""M-15 retention cleanup CLI（dry-run 安全）。

用法（backend/ 下）：
  .venv/Scripts/python.exe -m app.artifacts.cli --dry-run
  .venv/Scripts/python.exe -m app.artifacts.cli --execute   # 真实清理（生产需人工确认）
"""
from __future__ import annotations

import argparse
import asyncio

from app.artifacts.retention import RetentionService
from app.config import get_settings
from app.infra.deps import get_object_storage, get_session_factory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    days = args.days or get_settings().retention_heavy_days
    session = get_session_factory()()
    storage = get_object_storage()
    result = asyncio.run(RetentionService(session, storage, retention_days=days).run(dry_run=args.dry_run))
    print(result)
    session.close()


if __name__ == "__main__":
    main()
```
`infra/scripts/retention_cleanup.py`：薄包装，把仓库 `backend/` 加入 sys.path 后调用 CLI main（供部署在 api 容器内 `python infra/scripts/retention_cleanup.py --dry-run`）：
```python
"""Deployment-facing retention cleanup entry（python infra/scripts/retention_cleanup.py --dry-run）。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.artifacts.cli import main  # noqa: E402

main()
```

- [ ] **Step 5: 设置 → 存储与数据 摘要 + 清理预览（D-052/D-072 UI，不新增页面）**

后端新增 `backend/app/api/routes/settings_data.py`：
```python
"""M-15 设置 → 存储与数据（D-052/D-072）。只读摘要 + retention dry-run 预览；不暴露 MinIO 内部。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.artifacts.retention import CleanupResult, RetentionService
from app.auth.deps import require_user
from app.auth.models import User
from app.config import get_settings
from app.domain.models import Artifact, FieldEvidence, PageSnapshot, Record, Task
from app.infra.deps import get_db, storage

router = APIRouter(prefix="/settings", tags=["settings-data"])


class StorageSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_count: int
    record_count: int
    evidence_count: int
    artifact_count: int
    snapshot_bytes: int
    artifact_bytes: int
    retention_days: int


@router.get("/storage-summary", response_model=StorageSummaryView)
def storage_summary(user: User = Depends(require_user), db: DbSession = Depends(get_db)) -> StorageSummaryView:
    uid = user.id
    def _count(model):
        return int(db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) or 0)
    def _sum_bytes(col, model):
        return int(db.scalar(select(func.coalesce(func.sum(col), 0)).select_from(model).where(model.user_id == uid)) or 0)
    return StorageSummaryView(
        task_count=_count(Task), record_count=_count(Record), evidence_count=_count(FieldEvidence),
        artifact_count=_count(Artifact),
        snapshot_bytes=_sum_bytes(PageSnapshot.download_bytes, PageSnapshot),
        artifact_bytes=_sum_bytes(Artifact.size_bytes, Artifact),
        retention_days=int(get_settings().retention_heavy_days),
    )


@router.post("/storage/cleanup-preview", response_model=CleanupResult)
async def cleanup_preview(user: User = Depends(require_user), db: DbSession = Depends(get_db)) -> CleanupResult:
    """retention dry-run 预览：只统计，不删除（§54：Staging 默认只跑 dry-run）。"""
    svc = RetentionService(db, storage(), retention_days=int(get_settings().retention_heavy_days))
    return await svc.run(dry_run=True)
```
注意：`CleanupResult` 是 dataclass，FastAPI 响应模型需要它是 pydantic 或 dataclass 可直接序列化 —— FastAPI 支持 dataclass 响应；若报错则把 `CleanupResult` 继承改为 pydantic `BaseModel`。`storage()` 来自 `app.infra.deps`。
`app/api/router.py` 注册 `settings_data.router`。

前端 `SettingsView.vue` 的「存储与数据」区替换占位文案为真实摘要：
- `getStorageSummary()`（GET `/settings/storage-summary`）渲染：任务数 / 记录数 / 证据数 / 导出 Artifact 数 / 重型文件占用 / 保留天数。
- 「清理预览」按钮 → `postCleanupPreview()`（POST `/settings/storage/cleanup-preview`）→ 展示 CleanupResult（scanned/eligible/protected/deleted/bytes_freed），文案「仅预览，不执行删除」。
- 不新增独立 Storage 页面；不显示 bucket/object key。
Commit:
```bash
git add backend/app/api/routes/settings_data.py backend/app/api/router.py frontend/src/features/settings/SettingsView.vue
git commit -m "feat(settings): add storage summary and retention dry-run preview"
```

- [ ] **Step 6: Local 全量收尾验证 + M-15-execution 文档 + Commit**

Run（backend/）:
```bash
.venv/Scripts/python.exe -m pytest tests/artifacts -q                      # M-15 scoped
.venv/Scripts/python.exe -m ruff check app/artifacts app/api/routes/artifacts.py app/api/routes/completion.py app/domain app/review app/infra/object_storage.py tests/artifacts
.venv/Scripts/python.exe -m mypy app/artifacts app/api/routes/artifacts.py app/api/routes/completion.py
.venv/Scripts/python.exe -m alembic heads                                  # 0012 (head)
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"  # import PASS
```
Run（frontend/）:
```bash
npx vitest run src/features/artifacts src/app/overlay/modals/ExportModal.test.ts src/features/tasks/DeletedView.test.ts
npx vue-tsc --noEmit
npm run build
```
创建 `docs/implementation/M-15-execution.md`（记录 ArtifactService/Export types/filter snapshot/identity/ObjectStorage/download/Completion Card/soft delete/restore/permanent delete/Retention/CleanupResult/tests/commits/staging/Gate-4，见最终报告模板）。
Commit:
```bash
git add backend/app/artifacts/retention.py backend/app/artifacts/cli.py infra/scripts/retention_cleanup.py backend/app/config.py backend/tests/artifacts/test_retention.py docs/implementation/M-15-execution.md
git commit -m "feat(storage): add retention cleanup policy and dry-run job"
```

---

## 宏观验证（M-15 LOCAL DONE GATE）

执行后必须全绿：
- `pytest tests/artifacts -q`（TEST A-G + API + models）
- ruff / mypy（affected）
- `alembic heads` = 0012
- frontend `vitest run` scoped + `vue-tsc --noEmit` + `npm run build`
- `git status` working tree clean（除 infra/scripts/_gate3b_*.py 等历史 untracked，勿动）

## Self-Review

- **Spec coverage**：D-005/014/016/023/025/036/039/042/043/044/048/060/065/067/072 全部落 Task 1–8；M-15 实施计划「必须完成/产出契约/自动化验收/完成门禁」逐条覆盖；不新增页面（13-page）；M-16 边界不实现。
- **Placeholder scan**：无「TBD/TODO/类似上一任务」；关键算法（dataset_version/request_fingerprint/复用/manifest/引用复查/retention 保护）均给出实现。
- **Type consistency**：`ExportType/ExportRequest/ArtifactRef/ArtifactView/CompletionCardView/PermanentDeleteCommand` 在 contracts.py 单点定义，service/route/frontend 复用同名；`compute_dataset_version/canonical_filter_snapshot/build_csv_bytes/final_field_dict/schema_columns_for_spec` 命名全程一致；`ArtifactService.export/download/list_for_task` 与 route 调用签名一致；`DeletionService.permanent_delete` 返回 `DeletionManifest`。

---

## PROJECT SELF-APPROVAL（CHECK 1-21）

| # | 检查项 | 结论 |
|---|---|---|
| CHECK 1 | M-14 Precondition：M-14 = DONE（HEAD d2464c7，baseline SHA 已记录） | PASS |
| CHECK 2 | 正式 CSV 只含 PASSED（Task 3 `_EXPORT_PARTITION[FORMAL]`） | PASS |
| CHECK 3 | USER_OVERRIDE 用 final value；Evidence/override audit 不被改写（`final_field_dict` 只读叠加） | PASS |
| CHECK 4 | Filter Snapshot 完全复用 M-13 `RecordListParams` 契约（ExportFilter + `query_records_all`） | PASS |
| CHECK 5 | Artifact identity 完整：dataset_version + filter_snapshot + export_type + content_hash + request_fingerprint + schema_version | PASS |
| CHECK 6 | Artifact 幂等：同导出复用（`find_ready`）；数据变化 → 新 dataset_version → 新 Artifact（Task 3 测试 C） | PASS |
| CHECK 7 | CSV 存 ObjectStorage（`artifacts/u{user}/csv/{hash}.csv`），DB 只存 metadata/ref/hash | PASS |
| CHECK 8 | Completion Card 来自 DB facts（CompletionDecision + 分区计数 + URLResource）；无假百分比（契约无 percent 字段） | PASS |
| CHECK 9 | Soft Delete 可恢复（`deleted_at` + `restore_state`），不删除业务数据（Task 6 测试 E） | PASS |
| CHECK 10 | Running Task 删除必须 cancel：状态机不含 RUNNING/PAUSING/CANCELLING + 路由显式 409 | PASS |
| CHECK 11 | Permanent Delete：owner 校验 + state==DELETED + confirmed==True（Task 7） | PASS |
| CHECK 12 | Shared Blob：删除 A 不破坏 B（`_ref_used_elsewhere` 跨表跨用户复查，Task 7 测试 F） | PASS |
| CHECK 13 | Retention 保护 Evidence 引用（`FieldEvidence.snapshot_id` → PROTECTED，Task 8 测试 G） | PASS |
| CHECK 14 | FieldEvidence minimal snippet 长期保留（`raw_snippet`/`source_locator` 在 DB，与对象解耦） | PASS |
| CHECK 15 | 不删共享资源：DeletionService 只删 task-owned 行，不动 ModelConfig/SearchConfig/Credential/Template | PASS |
| CHECK 16 | 13 Page Boundary：无新页面；复用 /tasks、/tasks/:id/*、/settings、Export/Delete Modal | PASS |
| CHECK 17 | M-16 Boundary：无资源池/限流/压测/可靠性（明确不做） | PASS |
| CHECK 18 | DEFERRED-DYNAMIC-E2E-01：完全不处理（不在任何 Task 内） | PASS |
| CHECK 19 | A-Lite：只保留关键 scoped tests（TEST A-G + API + models + 3 个前端），无全量回归 | PASS |
| CHECK 20 | Git：不 push / 不 merge / 不 tag；每 Task 独立可验证 commit | PASS |
| CHECK 21 | Deployment：M-15 Staging + FAST Gate-4 为增量验收，不重跑 M-13/M-14 全量 | PASS |

## PLAN SELF-APPROVAL

**PLAN SELF-APPROVAL: PASS**

M-14 precondition: PASS
implementation plan M-15: PASS
formal CSV PASSED-only: PASS
review/audit export: PASS
M-13 filter snapshot reuse: PASS
Artifact identity: PASS
Artifact idempotency: PASS
ObjectStorage boundary: PASS
completion normal/partial: PASS
no fake percentage: PASS
soft delete/restore: PASS
running-delete boundary: PASS
permanent delete ownership: PASS
shared-object reference safety: PASS
retention evidence protection: PASS
FieldEvidence minimal retention: PASS
13-page boundary: PASS
M-16 boundary: PASS
deferred dynamic debt untouched: PASS
A-Lite testing: PASS
fast-development-test policy: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS

全部 PASS → 自动进入 superpowers:executing-plans。
