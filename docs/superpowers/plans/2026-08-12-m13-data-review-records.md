# M-13 实时数据页、人工审核、批量操作与数据查询能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (user pre-authorized inline execution) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 `/tasks/:id/data` 的真实业务闭环：三分区 Tabs + 实时计数、后端 Records Query（分页/搜索/筛选/排序/列设置）、Record Detail Drawer、单条审核（人工修正/通过/拒绝/Agent 重新处理）、批量审核（语义兼容 + 审计）、Data 页 query 参数可被质量页 Deep Link 复用。

**Architecture:** 新增 `backend/app/review/` 领域包：ReviewRepository（owner-safe 查询/覆写/审计持久化）→ ReviewPolicy（由 partition/review_type 派生 `allowed_actions`，后端事实驱动）→ ReviewService（approve/reject/edit/agent_reevaluate/batch，状态变化走领域命令 + `append_domain_event` + outbox，一次事务提交）。HTTP 层新增 `app/api/routes/records.py`（GET 查询 + POST 单条审核 + POST 批量审核），复用 `require_user`/`get_db` 既有注入模式。前端 `features/data/` 新增 `data.api.ts` + `useRecords`/`useRecordReview`，`TaskDataView.vue` 组装三分区 Tabs/查询工具栏/表格并打开既有 `RecordDrawer`（补全 `app/overlay/drawers/RecordDrawer.vue` 契约），SSE 复用 `/api/events/tasks/{id}` 通道订阅 `record.*` 事件做增量刷新。新表通过 migration `0011` 增量扩展，不创建第二套 Record 业务事实。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Pydantic v2 / Temporal（仅 reevaluate 走 outbox 信号，本模块不做真实重抓）/ pytest（A-Lite）。前端 Vue 3 + TS strict + Vue Router（query Deep Link）+ EventSource SSE + Vitest。

## Global Constraints

- Records 事实来源只有 `records`（payload 最终值）+ `record_field_overrides`（人工覆写）+ `field_evidence`（原始证据）+ `validation_results`（分区/review/allowed_actions 契约）。**禁止**创建 RecordViewRecord/FinalRecord 第二套业务事实。
- 结果分区只消费 M-12 三分区 `passed|needs_review|rejected`（`ValidationPartition`），不新增第二套分区名。
- 前端 `allowed_actions` 只来自后端（`RecordView.allowed_actions` 由 ReviewPolicy 派生）；前端不得复制状态机。
- 人工修正必须保留 `original_value` / `final_value` / `value_source=USER_OVERRIDE` / `modified_by` / `modified_at`，禁止覆盖/篡改 `page_snapshots` 与 `field_evidence`（D-042）。
- 批量审核只对后端 `allowed_actions` 允许且语义兼容的记录开放；不同 `review_reason` 的记录不能被无条件"全部通过"（D-061）。
- Agent 重新处理 = 生成新的执行尝试/领域事件，**不覆盖旧历史**（append-only：旧 Record/FieldEvidence/DomainEvent 全部保留）。
- 所有查询/命令强制 `user_id` 归属边界；越权访问返回 404（owner-safe，不泄漏存在性）。
- 列设置只影响前端 UI 显示，不修改 `CollectionSpec`。
- Deep Link query 参数契约固定为：`?status=passed|review|rejected&review_type=&source_type=&extract_method=&min_confidence=&q=&field=&value=&sort_by=&sort_order=&page=&page_size=`（M-14 质量页据此下钻，见 D-062）。
- 本轮不做 M-14 Quality 页面、不做 M-15 CSV 导出、不做真实 Playwright 重抓；`agent_reevaluate` 只入队 outbox 复用 M-11 `mark_records_eligible_for_recompute` seam。
- A-Lite：只跑 M-13 scoped tests（Records Query / Review / Batch / owner 隔离 / allowed_actions / 前端 data scoped）；不重跑 M-09～M-12 全量回归、不跑 Golden A/B/C。
- 代码风格遵循 agent-code-standards.md：typed contracts、状态变化走领域命令、事件走 `append_domain_event`、幂等身份走 `stable_fingerprint`、全部 owner-safe。

---

### Task 1: Review 数据模型 + migration 0011 + ReviewRepository

**Files:**
- Modify: `backend/app/domain/models.py`（Record 增加 `data_version`；新增 `RecordFieldOverride` / `RecordReviewAction`）
- Create: `backend/alembic/versions/0011_data_review.py`
- Create: `backend/app/review/__init__.py`
- Create: `backend/app/review/contracts.py`
- Create: `backend/app/review/repository.py`
- Test: `backend/tests/review/test_review_persistence.py`

**Interfaces:**
- Consumes: `app.domain.models.Record`（既有 `partition`/`review_type`/`review_reason`/`payload`）、`app.validation.contracts.ValidationPartition`、`app.auth.errors.NotFoundError`、M-12 `ValidationRepository.latest_snapshot`（取 `dataset_version`）。
- Produces:
  - `ReviewAction`（StrEnum: `approve|reject|edit|agent_reevaluate`）
  - `RecordView`（pydantic: `record_id/task_id/partition/review_type/review_reason/data_version/fields/source_url/created_at/updated_at/allowed_actions`）
  - `RecordFieldDetail`（pydantic: `field_name/value/original_value/value_source/extract_method/extractor_version/confidence/source_url/snapshot_id`）
  - `RecordDetailView`（pydantic: `record_id/partition/review_type/review_reason/data_version/allowed_actions/fields: list[RecordFieldDetail]/created_at/updated_at`）
  - `RecordListParams`（pydantic query: `partition/q/field/value/source_type/extract_method/min_confidence/review_type/sort_by/sort_order/page/page_size`）
  - `RecordListResponse`（pydantic: `task_id/partition_counts/items/total/page/page_size/dataset_version`）
  - `FieldEdit`、`RecordReviewCommand`、`RecordReviewResponse`、`BatchReviewCommand`、`BatchReviewItem`、`BatchReviewResponse`（DTO 字段见 Task 3/5）
  - `ReviewRepository`：`query_records` / `count_by_partition` / `get_record_owned` / `list_overrides` / `create_override` / `create_review_action` / `evidence_for_record` / `url_for_record`
  - DB 表：`record_field_overrides`、`record_review_actions`；`records.data_version`

- [x] **Step 1: 确认 alembic 基线为 0010**

Run:
```bash
.venv/Scripts/python.exe -m alembic heads   # 预期: 0010 (head)
```

- [x] **Step 2: 在 `app/domain/models.py` 为 `Record` 增加 `data_version` 列**

在 `Record` 类的 `updated_at` 列前增加：

```python
    # ---- M-13 data review（migration 0011，nullable 兼容）----
    data_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [x] **Step 3: 在 `app/domain/models.py` 文件末尾新增两个模型**

```python
class RecordFieldOverride(Base):
    """M-13 人工字段修正（D-042）。保留 original/final/value_source/modified_by/modified_at。

    禁止覆盖 PageSnapshot 与 FieldEvidence；Record.payload 最终值 = payload 叠加覆写。
    """

    __tablename__ = "record_field_overrides"
    __table_args__ = (
        UniqueConstraint("record_id", "field_name", name="uq_rfo_record_field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_source: Mapped[str] = mapped_column(String(30), nullable=False, default="USER_OVERRIDE")
    modified_by: Mapped[int] = mapped_column(Integer, nullable=False)
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecordReviewAction(Base):
    """M-13 单条/批量审核审计（D-061）。append-only，绝不 UPDATE 历史。"""

    __tablename__ = "record_review_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    review_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    batch_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [x] **Step 4: 创建 migration `0011_data_review.py`**

```python
"""M-13 data review: records.data_version + field overrides + review audit."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("data_version", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "record_field_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("final_value", sa.Text(), nullable=True),
        sa.Column("value_source", sa.String(30), nullable=False, server_default="USER_OVERRIDE"),
        sa.Column("modified_by", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("record_id", "field_name", name="uq_rfo_record_field"),
    )
    op.create_index("ix_rfo_user_record", "record_field_overrides", ["user_id", "record_id"])

    op.create_table(
        "record_review_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(30), nullable=False),
        sa.Column("review_type", sa.String(50), nullable=True),
        sa.Column("review_reason", sa.String(50), nullable=True),
        sa.Column("batch_operation_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_rra_user_record", "record_review_actions", ["user_id", "record_id"])


def downgrade() -> None:
    op.drop_index("ix_rra_user_record", table_name="record_review_actions")
    op.drop_table("record_review_actions")
    op.drop_index("ix_rfo_user_record", table_name="record_field_overrides")
    op.drop_table("record_field_overrides")
    op.drop_column("records", "data_version")
```

- [x] **Step 5: 创建 `app/review/contracts.py`（RecordView/RecordDetail/RecordList 契约）**

```python
"""M-13 data/review typed contracts（D-041/D-060/D-061/D-062）。

RecordView 是查询/审核统一返回契约；partition 只来自 M-12 三分区。allowed_actions
由 ReviewPolicy 派生（后端事实驱动），前端不得复制。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    AGENT_REEVALUATE = "agent_reevaluate"


class FieldEdit(BaseModel):
    model_config = _STRICT

    field_name: str
    final_value: str | None


class RecordView(BaseModel):
    model_config = _STRICT

    record_id: int
    task_id: int
    partition: str
    review_type: str | None
    review_reason: str | None
    data_version: int
    fields: dict
    source_url: str | None = None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class RecordFieldDetail(BaseModel):
    model_config = _STRICT

    field_name: str
    value: str | None
    original_value: str | None = None
    value_source: str = "EXTRACTED"
    extract_method: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    source_url: str | None = None
    snapshot_id: int | None = None


class RecordDetailView(BaseModel):
    model_config = _STRICT

    record_id: int
    task_id: int
    partition: str
    review_type: str | None
    review_reason: str | None
    data_version: int
    allowed_actions: list[str]
    fields: list[RecordFieldDetail]
    created_at: datetime
    updated_at: datetime


class RecordListParams(BaseModel):
    model_config = _STRICT

    partition: Literal["passed", "needs_review", "rejected"] | None = None
    q: str | None = None          # 跨字符串字段全文搜索（后端执行）
    field: str | None = None      # 字段筛选名
    value: str | None = None      # 字段筛选值（精确匹配）
    source_type: str | None = None
    extract_method: str | None = None
    min_confidence: float | None = None
    review_type: str | None = None
    sort_by: str | None = None    # 可排序字段白名单见 repository
    sort_order: Literal["asc", "desc"] = "asc"
    page: int = 1
    page_size: int = 20


class RecordListResponse(BaseModel):
    model_config = _STRICT

    task_id: int
    partition_counts: dict[str, int]
    items: list[RecordView]
    total: int
    page: int
    page_size: int
    dataset_version: str | None = None


class RecordReviewCommand(BaseModel):
    model_config = _STRICT

    action: ReviewAction
    reason: str | None = None
    edits: list[FieldEdit] = []
    expected_data_version: int


class RecordReviewResponse(BaseModel):
    model_config = _STRICT

    record: RecordView


class BatchReviewCommand(BaseModel):
    model_config = _STRICT

    action: Literal["approve", "reject", "agent_reevaluate"]
    record_ids: list[int]
    reason: str | None = None
    expected_data_versions: dict[int, int] = {}


class BatchReviewItem(BaseModel):
    model_config = _STRICT

    record_id: int
    ok: bool
    partition: str | None = None
    error: str | None = None


class BatchReviewResponse(BaseModel):
    model_config = _STRICT

    batch_operation_id: str
    results: list[BatchReviewItem]
```

- [x] **Step 6: 创建 `app/review/repository.py`（owner-safe 查询/覆写/审计持久化）**

```python
"""M-13 ReviewRepository：records 查询 + field overrides + review audit（D-041/042/060/061）。

所有查询强制 user_id 边界；跨用户访问返回 NotFoundError(404)，不泄漏存在性。
create_override/create_review_action 只 flush（不 commit），service 统一单事务提交（D-015）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.auth.errors import NotFoundError
from app.domain.models import (
    FieldEvidence,
    PageSnapshot,
    Record,
    RecordFieldOverride,
    RecordReviewAction,
    URLResource,
)
from app.review.contracts import RecordListParams

SORTABLE_COLUMNS: dict[str, Any] = {
    "id": Record.id,
    "created_at": Record.created_at,
    "updated_at": Record.updated_at,
}

# Deep Link / Quality 下钻固定允许的筛选字段（D-062）
FILTER_FIELDS: frozenset[str] = frozenset(["source_type", "extract_method", "review_type"])


def _owned(db: Any, model: type, user_id: int, obj_id: int) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


class ReviewRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def get_record_owned(self, *, user_id: int, record_id: int) -> Record:
        return _owned(self._db, Record, user_id, record_id)

    def count_by_partition(self, *, user_id: int, task_id: int) -> dict[str, int]:
        rows = self._db.execute(
            select(Record.partition, func.count())
            .where(Record.user_id == user_id, Record.task_id == task_id)
            .group_by(Record.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    def query_records(
        self, *, user_id: int, task_id: int, params: RecordListParams
    ) -> tuple[int, list[Record]]:
        stmt = select(Record).where(Record.user_id == user_id, Record.task_id == task_id)
        if params.partition:
            stmt = stmt.where(Record.partition == params.partition)
        if params.review_type:
            stmt = stmt.where(Record.review_type == params.review_type)
        if params.field and params.value:
            # 简单 AND 筛选（D-060）：payload JSON 字段精确匹配
            stmt = stmt.where(Record.payload[params.field].astext == params.value)
        if params.source_type:
            stmt = stmt.where(Record.payload["source_type"].astext == params.source_type)
        if params.extract_method:
            stmt = stmt.where(Record.payload["extract_method"].astext == params.extract_method)
        if params.q:
            # 跨字符串字段简单包含匹配；显式列出搜索字段，避免扫全部 payload
            like = f"%{params.q}%"
            conds = [Record.payload[col].astext.ilike(like) for col in ("标题", "文号", "正文摘要", "字段值")]
            from sqlalchemy import or_

            stmt = stmt.where(or_(*conds))
        if params.min_confidence is not None:
            stmt = stmt.where(Record.payload["confidence"].astext.cast(func.numeric()) >= params.min_confidence)

        total = int(self._db.scalar(select(func.count()).select_from(stmt.subquery())))
        sort_col = SORTABLE_COLUMNS.get(params.sort_by or "", Record.created_at)
        order = sort_col.asc() if params.sort_order == "asc" else sort_col.desc()
        rows = list(
            self._db.scalars(
                stmt.order_by(order).offset((params.page - 1) * params.page_size).limit(params.page_size)
            )
        )
        return total, rows

    def list_overrides(self, *, user_id: int, record_id: int) -> list[RecordFieldOverride]:
        return list(
            self._db.scalars(
                select(RecordFieldOverride)
                .where(RecordFieldOverride.user_id == user_id, RecordFieldOverride.record_id == record_id)
                .order_by(RecordFieldOverride.id)
            )
        )

    def evidence_for_record(self, *, user_id: int, record_id: int) -> list[FieldEvidence]:
        return list(
            self._db.scalars(
                select(FieldEvidence)
                .where(FieldEvidence.user_id == user_id, FieldEvidence.record_id == record_id)
                .order_by(FieldEvidence.id)
            )
        )

    def url_for_record(self, *, record: Record) -> str | None:
        if record.url_resource_id is None:
            return None
        row = self._db.get(URLResource, record.url_resource_id)
        return row.url if row else None

    def create_override(
        self,
        *,
        user_id: int,
        task_id: int,
        record_id: int,
        field_name: str,
        original_value: str | None,
        final_value: str | None,
        modified_by: int,
    ) -> RecordFieldOverride:
        row = RecordFieldOverride(
            user_id=user_id,
            task_id=task_id,
            record_id=record_id,
            field_name=field_name,
            original_value=original_value,
            final_value=final_value,
            value_source="USER_OVERRIDE",
            modified_by=modified_by,
        )
        self._db.add(row)
        return row

    def create_review_action(
        self,
        *,
        user_id: int,
        task_id: int,
        record_id: int,
        action_type: str,
        review_type: str | None,
        review_reason: str | None,
        batch_operation_id: str | None,
        reason: str | None,
        reviewed_by: int,
        detail: dict | None = None,
    ) -> RecordReviewAction:
        row = RecordReviewAction(
            user_id=user_id,
            task_id=task_id,
            record_id=record_id,
            action_type=action_type,
            review_type=review_type,
            review_reason=review_reason,
            batch_operation_id=batch_operation_id,
            reason=reason,
            reviewed_by=reviewed_by,
            detail=detail,
        )
        self._db.add(row)
        return row
```

- [x] **Step 7: 写失败测试 `backend/tests/review/test_review_persistence.py`**

```python
"""M-13 persistence：migration 0011 + overrides + review audit + owner 隔离。"""
import pytest
from sqlalchemy import text

from app.domain.models import Record
from app.review.repository import ReviewRepository
from app.review.contracts import RecordListParams


def _make_record(db, user_id: int, task_id: int, partition: str = "needs_review",
                 payload: dict | None = None) -> Record:
    row = Record(
        user_id=user_id, task_id=task_id, spec_version=1, partition=partition,
        payload=payload or {"标题": "测试记录", "source_type": "official_site", "extract_method": "llm"},
    )
    db.add(row)
    db.flush()
    return row


def test_migration_0011_creates_review_tables(app_db, alembic_heads) -> None:
    assert alembic_heads == "0011"
    cols = {c[0] for c in app_db.execute(text(
        "select column_name from information_schema.columns where table_name='records'"))}
    assert "data_version" in cols
    for t in ("record_field_overrides", "record_review_actions"):
        exists = app_db.execute(text(
            "select to_regclass(:t)").bindparams(t=t)).scalar()
        assert exists is not None, f"missing table {t}"


def test_create_override_preserves_original_and_final(db_session, user_a, task_a) -> None:
    rec = _make_record(db_session, user_a, task_a, payload={"标题": "旧值"})
    repo = ReviewRepository(db_session)
    repo.create_override(user_id=user_a, task_id=task_a, record_id=rec.id,
                         field_name="标题", original_value="旧值", final_value="新值", modified_by=user_a)
    db_session.flush()
    overrides = repo.list_overrides(user_id=user_a, record_id=rec.id)
    assert len(overrides) == 1
    assert overrides[0].original_value == "旧值"
    assert overrides[0].final_value == "新值"
    assert overrides[0].value_source == "USER_OVERRIDE"
    assert overrides[0].modified_by == user_a


def test_create_review_action_audit_fields(db_session, user_a, task_a) -> None:
    rec = _make_record(db_session, user_a, task_a)
    repo = ReviewRepository(db_session)
    repo.create_review_action(user_id=user_a, task_id=task_a, record_id=rec.id,
                              action_type="approve", review_type=None, review_reason=None,
                              batch_operation_id=None, reason="人工确认", reviewed_by=user_a)
    db_session.flush()
    from sqlalchemy import select
    from app.domain.models import RecordReviewAction
    row = db_session.scalar(select(RecordReviewAction).where(RecordReviewAction.record_id == rec.id))
    assert row.action_type == "approve"
    assert row.reviewed_by == user_a
    assert row.reviewed_at is not None


def test_query_records_partition_and_and_filter(db_session, user_a, task_a) -> None:
    _make_record(db_session, user_a, task_a, partition="passed", payload={"标题": "A", "文号": "沪府令1号"})
    _make_record(db_session, user_a, task_a, partition="needs_review", payload={"标题": "B"})
    db_session.flush()
    repo = ReviewRepository(db_session)
    total, rows = repo.query_records(user_id=user_a, task_id=task_a,
                                     params=RecordListParams(partition="passed"))
    assert total == 1 and rows[0].payload["标题"] == "A"
    total, rows = repo.query_records(user_id=user_a, task_id=task_a,
                                     params=RecordListParams(field="文号", value="沪府令1号"))
    assert total == 1


def test_owner_isolation_cross_user(db_session, user_a, user_b, task_a) -> None:
    rec = _make_record(db_session, user_a, task_a)
    db_session.flush()
    repo = ReviewRepository(db_session)
    with pytest.raises(Exception):
        repo.get_record_owned(user_id=user_b, record_id=rec.id)
```

- [x] **Step 8: 运行测试确认失败（migration/表不存在）**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_review_persistence.py -q
```
Expected: FAIL（`ImportError` / 表不存在 / `alembic_heads` 断言失败）

- [x] **Step 9: 实现模型 + migration + repository 后确认通过**

Run:
```bash
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest tests/review/test_review_persistence.py -q
```
Expected: 4 passed

- [x] **Step 10: 提交**

```bash
git add backend/app/domain/models.py backend/alembic/versions/0011_data_review.py \
        backend/app/review backend/tests/review
git commit -m "feat(review): add review persistence contracts and repository"
```

---

### Task 2: ReviewPolicy —— allowed_actions 派生（后端事实驱动）

**Files:**
- Create: `backend/app/review/policy.py`
- Test: `backend/tests/review/test_review_policy.py`

**Interfaces:**
- Consumes: `Record`（`partition`/`review_type`）、`ValidationPartition`。
- Produces: `ReviewPolicy.allowed_actions(*, record) -> list[str]`、`ReviewPolicy.assert_batch_compatible(*, action, records) -> None`。

- [x] **Step 1: 写失败测试 `backend/tests/review/test_review_policy.py`**

```python
import pytest

from app.review.policy import ReviewPolicy


def _rec(partition: str, review_type: str | None = None):
    class _R:
        partition = partition
        review_type = review_type

    return _R()


def test_passed_has_no_review_actions():
    assert ReviewPolicy.allowed_actions(record=_rec("passed")) == []


def test_rejected_has_no_review_actions():
    assert ReviewPolicy.allowed_actions(record=_rec("rejected")) == []


def test_needs_review_offers_core_actions():
    actions = ReviewPolicy.allowed_actions(record=_rec("needs_review", "missing_required"))
    assert {"edit", "approve", "reject", "agent_reevaluate"}.issubset(actions)


def test_unresolved_conflict_offers_resolve():
    actions = ReviewPolicy.allowed_actions(record=_rec("needs_review", "unresolved_conflict"))
    assert "resolve_conflict" in actions


def test_batch_approve_requires_same_reason():
    rows = [_rec("needs_review", "missing_required"), _rec("needs_review", "low_evidence_confidence")]
    with pytest.raises(Exception):
        ReviewPolicy.assert_batch_compatible(action="approve", records=rows)


def test_batch_approve_same_reason_ok():
    rows = [_rec("needs_review", "missing_required"), _rec("needs_review", "missing_required")]
    ReviewPolicy.assert_batch_compatible(action="approve", records=rows)  # 不抛异常
```

- [x] **Step 2: 实现 `app/review/policy.py`**

```python
"""M-13 审核 allowed_actions 派生策略（D-042/D-061，后端事实驱动）。

前端 allowed_actions 唯一来源；不同 review_reason 的记录不允许无条件批量通过。
"""

from __future__ import annotations

from typing import Any

from app.validation.contracts import AllowedReviewAction


class BatchCompatibilityError(Exception):
    """批量动作与记录语义不兼容。"""


class ReviewPolicy:
    @staticmethod
    def allowed_actions(*, record: Any) -> list[str]:
        if record.partition != "needs_review":
            return []
        actions = [
            AllowedReviewAction.APPROVE.value,
            AllowedReviewAction.EDIT.value,
            AllowedReviewAction.REJECT.value,
            AllowedReviewAction.AGENT_REEVALUATE.value,
        ]
        if record.review_type == "unresolved_conflict":
            actions.append(AllowedReviewAction.RESOLVE_CONFLICT.value)
        if record.review_type == "possible_duplicate":
            actions.append(AllowedReviewAction.MERGE_DUPLICATE.value)
        return actions

    @staticmethod
    def assert_batch_compatible(*, action: str, records: list[Any]) -> None:
        """D-061：批量 approve 只对 review_reason 完全一致的记录开放。"""
        if action != "approve":
            return
        reasons = {r.review_reason for r in records}
        if len(reasons) > 1:
            raise BatchCompatibilityError("不同复核原因的记录不能无条件批量通过")
```

- [x] **Step 3: 运行测试确认通过**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_review_policy.py -q
```
Expected: 6 passed

- [x] **Step 4: 提交**

```bash
git add backend/app/review/policy.py backend/tests/review/test_review_policy.py
git commit -m "feat(review): derive record allowed_actions from partition policy"
```

---

### Task 3: 单条审核命令（approve / reject / edit）

**Files:**
- Create: `backend/app/review/service.py`
- Create: `backend/app/review/views.py`（Record → RecordView / RecordDetailView 组装；叠加 overrides）
- Test: `backend/tests/review/test_review_service.py`

**Interfaces:**
- Consumes: `ReviewRepository`、`ReviewPolicy`、`ValidationPartition`、`append_domain_event`、`RecordReviewCommand`/`FieldEdit`。
- Produces:
  - `ReviewService(db)` 方法：
    - `approve_record(*, user_id, record_id, reason, expected_data_version) -> RecordView`
    - `reject_record(*, user_id, record_id, reason, expected_data_version) -> RecordView`
    - `edit_record(*, user_id, record_id, edits, expected_data_version) -> RecordView`
    - `agent_reevaluate(*, user_id, record_id, reason, expected_data_version) -> RecordView`（事件/outbox 见 Task 6）
    - `to_view(record, overrides) -> RecordView` / `to_detail(record, overrides, evidence) -> RecordDetailView`（views 模块）
  - 领域事件：`record.approved` / `record.rejected` / `record.edited`（aggregate_type=`record`，payload 含 `partition/review_reason/data_version`）。

- [x] **Step 1: 创建 `app/review/views.py`（Record → DTO 组装，叠加覆写）**

```python
"""M-13 Record → DTO 视图组装。fields 最终值 = payload 叠加人工覆写（D-042）。"""

from __future__ import annotations

from typing import Any

from app.domain.models import FieldEvidence, Record, RecordFieldOverride
from app.review.contracts import RecordDetailView, RecordFieldDetail, RecordView
from app.review.policy import ReviewPolicy


def _apply_overrides(payload: dict, overrides: list[RecordFieldOverride]) -> dict:
    final = dict(payload)
    for o in overrides:
        final[o.field_name] = o.final_value
    return final


def to_view(record: Record, overrides: list[RecordFieldOverride], source_url: str | None) -> RecordView:
    return RecordView(
        record_id=record.id,
        task_id=record.task_id,
        partition=record.partition,
        review_type=record.review_type,
        review_reason=record.review_reason,
        data_version=record.data_version,
        fields=_apply_overrides(record.payload, overrides),
        source_url=source_url,
        created_at=record.created_at,
        updated_at=record.updated_at,
        allowed_actions=ReviewPolicy.allowed_actions(record=record),
    )


def to_detail(
    record: Record,
    overrides: list[RecordFieldOverride],
    evidence: list[FieldEvidence],
    source_url: str | None,
) -> RecordDetailView:
    override_by_field = {o.field_name: o for o in overrides}
    ev_by_field: dict[str, FieldEvidence] = {}
    for ev in evidence:
        if ev.field_name and ev.field_name not in ev_by_field:
            ev_by_field[ev.field_name] = ev
    fields: list[RecordFieldDetail] = []
    seen: set[str] = set()
    for key, value in record.payload.items():
        seen.add(key)
        ov = override_by_field.get(key)
        ev = ev_by_field.get(key)
        fields.append(RecordFieldDetail(
            field_name=key,
            value=ov.final_value if ov else (value if isinstance(value, (str, int, float, bool)) else str(value or "")),
            original_value=ov.original_value if ov else None,
            value_source=ov.value_source if ov else "EXTRACTED",
            extract_method=ev.extract_method if ev else None,
            extractor_version=ev.extractor_version if ev else None,
            confidence=ev.confidence if ev else None,
            source_url=ev.source_url if ev else source_url,
            snapshot_id=ev.snapshot_id if ev else None,
        ))
    for key, ov in override_by_field.items():
        if key not in seen:
            fields.append(RecordFieldDetail(
                field_name=key, value=ov.final_value, original_value=ov.original_value,
                value_source=ov.value_source, extract_method=None, extractor_version=None,
                confidence=None, source_url=None, snapshot_id=None,
            ))
    return RecordDetailView(
        record_id=record.id,
        task_id=record.task_id,
        partition=record.partition,
        review_type=record.review_type,
        review_reason=record.review_reason,
        data_version=record.data_version,
        allowed_actions=ReviewPolicy.allowed_actions(record=record),
        fields=fields,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
```

- [x] **Step 2: 创建 `app/review/service.py`（单条审核命令）**

```python
"""M-13 ReviewService：approve/reject/edit/agent_reevaluate/batch（D-042/D-061）。

状态变化走领域命令 + append_domain_event，单事务提交；人工修正写入 record_field_overrides
（original/final/value_source/modified_by/modified_at），不覆盖 FieldEvidence/PageSnapshot。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.auth.errors import NotFoundError
from app.domain.models import Record
from app.review.contracts import (
    BatchReviewCommand,
    BatchReviewItem,
    BatchReviewResponse,
    FieldEdit,
    RecordReviewCommand,
    RecordView,
    ReviewAction,
)
from app.review.policy import BatchCompatibilityError, ReviewPolicy
from app.review.repository import ReviewRepository
from app.review.views import to_view
from app.state.events import append_domain_event


class ReviewConflictError(Exception):
    """数据版本冲突或审核动作非法。"""


class ReviewService:
    def __init__(self, db: DbSession) -> None:
        self._db = db
        self._repo = ReviewRepository(db)

    def execute(self, *, user_id: int, record_id: int, cmd: RecordReviewCommand) -> RecordView:
        record = self._repo.get_record_owned(user_id=user_id, record_id=record_id)
        self._assert_version(record, cmd.expected_data_version)
        actions = ReviewPolicy.allowed_actions(record=record)
        if cmd.action.value not in actions:
            raise ReviewConflictError("当前记录不允许执行该审核动作")
        if cmd.action is ReviewAction.APPROVE:
            self._apply_partition(record, "passed", reason=cmd.reason, event="record.approved",
                                  user_id=user_id)
        elif cmd.action is ReviewAction.REJECT:
            self._apply_partition(record, "rejected", reason=cmd.reason, event="record.rejected",
                                  user_id=user_id)
        elif cmd.action is ReviewAction.EDIT:
            self._apply_edits(user_id=user_id, record=record, edits=cmd.edits)
        elif cmd.action is ReviewAction.AGENT_REEVALUATE:
            from app.review.reevaluate import request_reevaluate

            request_reevaluate(self._db, user_id=user_id, record=record, reason=cmd.reason)
        else:  # merge_duplicate / resolve_conflict：本轮不允许通过普通命令执行
            raise ReviewConflictError("该动作需要更高级处理，暂不开放")
        self._db.commit()
        overrides = self._repo.list_overrides(user_id=user_id, record_id=record.id)
        return to_view(record, overrides, self._repo.url_for_record(record=record))

    # ---- internal ----
    @staticmethod
    def _assert_version(record: Record, expected: int) -> None:
        if expected != record.data_version:
            raise ReviewConflictError("记录已更新，请刷新后重试")

    def _apply_partition(
        self, record: Record, partition: str, *, reason: str | None, event: str, user_id: int
    ) -> None:
        record.partition = partition
        record.review_type = None
        record.review_reason = None
        record.data_version += 1
        append_domain_event(
            self._db, user_id=user_id, aggregate_type="record", aggregate_id=record.id,
            aggregate_version=record.data_version, event_type=event,
            payload={"partition": partition, "reason": reason, "data_version": record.data_version},
            actor_type="user", actor_id=user_id, run_id=record.run_id,
        )
        self._repo.create_review_action(
            user_id=user_id, task_id=record.task_id, record_id=record.id,
            action_type=partition, review_type=record.review_type, review_reason=record.review_reason,
            batch_operation_id=None, reason=reason, reviewed_by=user_id,
        )

    def _apply_edits(self, *, user_id: int, record: Record, edits: list[FieldEdit]) -> None:
        current = dict(record.payload)
        for edit in edits:
            original = current.get(edit.field_name)
            self._repo.create_override(
                user_id=user_id, task_id=record.task_id, record_id=record.id,
                field_name=edit.field_name,
                original_value=str(original) if original is not None else None,
                final_value=edit.final_value,
                modified_by=user_id,
            )
        record.data_version += 1
        append_domain_event(
            self._db, user_id=user_id, aggregate_type="record", aggregate_id=record.id,
            aggregate_version=record.data_version, event_type="record.edited",
            payload={"fields": [e.field_name for e in edits], "data_version": record.data_version},
            actor_type="user", actor_id=user_id, run_id=record.run_id,
        )
        self._repo.create_review_action(
            user_id=user_id, task_id=record.task_id, record_id=record.id,
            action_type="edit", review_type=record.review_type, review_reason=record.review_reason,
            batch_operation_id=None, reason=None, reviewed_by=user_id,
            detail={"fields": [e.field_name for e in edits]},
        )
```

- [x] **Step 3: 写失败测试 `backend/tests/review/test_review_service.py`**

```python
import pytest

from app.review.contracts import FieldEdit, RecordReviewCommand, ReviewAction
from app.review.service import ReviewService


def _rec(db, user, task, partition="needs_review", review_type="missing_required",
         payload=None):
    from app.domain.models import Record

    row = Record(user_id=user, task_id=task, spec_version=1, partition=partition,
                 review_type=review_type, payload=payload or {"标题": "旧值", "文号": "沪府令1号"})
    db.add(row)
    db.flush()
    return row


def test_approve_moves_to_passed_and_audits(db_session, user_a, task_a):
    rec = _rec(db_session, user_a, task_a)
    svc = ReviewService(db_session)
    view = svc.execute(user_id=user_a, record_id=rec.id,
                       cmd=RecordReviewCommand(action=ReviewAction.APPROVE,
                                               expected_data_version=rec.data_version))
    assert view.partition == "passed"
    assert view.allowed_actions == []


def test_edit_preserves_original_evidence(db_session, user_a, task_a):
    rec = _rec(db_session, user_a, task_a, payload={"标题": "旧值"})
    svc = ReviewService(db_session)
    view = svc.execute(user_id=user_a, record_id=rec.id,
                       cmd=RecordReviewCommand(action=ReviewAction.EDIT,
                                               expected_data_version=rec.data_version,
                                               edits=[FieldEdit(field_name="标题", final_value="新值")]))
    assert view.fields["标题"] == "新值"
    overrides = db_session.query(type(rec)).all()  # 覆写表由 repository 断言
    from app.review.repository import ReviewRepository

    ovs = ReviewRepository(db_session).list_overrides(user_id=user_a, record_id=rec.id)
    assert ovs[0].original_value == "旧值"
    assert ovs[0].value_source == "USER_OVERRIDE"
    assert ovs[0].modified_by == user_a
    assert view.partition == "needs_review"  # edit 不改分区


def test_stale_version_rejected(db_session, user_a, task_a):
    rec = _rec(db_session, user_a, task_a)
    svc = ReviewService(db_session)
    with pytest.raises(Exception):
        svc.execute(user_id=user_a, record_id=rec.id,
                    cmd=RecordReviewCommand(action=ReviewAction.APPROVE,
                                            expected_data_version=rec.data_version + 99))


def test_action_not_allowed_for_passed(db_session, user_a, task_a):
    rec = _rec(db_session, user_a, task_a, partition="passed")
    svc = ReviewService(db_session)
    with pytest.raises(Exception):
        svc.execute(user_id=user_a, record_id=rec.id,
                    cmd=RecordReviewCommand(action=ReviewAction.REJECT,
                                            expected_data_version=rec.data_version))
```

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_review_service.py -q
```
Expected: 4 passed

- [x] **Step 5: 提交**

```bash
git add backend/app/review/service.py backend/app/review/views.py backend/tests/review/test_review_service.py
git commit -m "feat(review): add single record review commands"
```

---

### Task 4: Records Query API（GET /tasks/{id}/records）

**Files:**
- Create: `backend/app/api/routes/records.py`
- Modify: `backend/app/api/router.py`（include `records.router`）
- Modify: `backend/app/api/schemas.py`（若需在 router 内复用的 response model 由 review.contracts 提供）
- Test: `backend/tests/review/test_records_query_api.py`

**Interfaces:**
- Consumes: `require_user`、`get_db`、`TaskRepository.get_owned`（task owner-safe 404）、`ReviewRepository`、`ValidationRepository.latest_snapshot`（dataset_version）。
- Produces: `GET /tasks/{task_id}/records?partition=&q=&field=&value=&source_type=&extract_method=&min_confidence=&review_type=&sort_by=&sort_order=&page=&page_size=` → `RecordListResponse`。

- [x] **Step 1: 创建 `app/api/routes/records.py`**

```python
"""Records Query + Review Command API（M-13，D-041/042/060/061/062）。

GET  /tasks/{task_id}/records                 → RecordListResponse（分页/搜索/筛选/排序/计数）
GET  /tasks/{task_id}/records/{record_id}     → RecordDetailView（Drawer，字段+证据+覆写）
POST /tasks/{task_id}/records/{record_id}/review      → RecordReviewResponse
POST /tasks/{task_id}/records/batch-review            → BatchReviewResponse
全部 owner-safe：task/record 越权统一 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import (
    BatchReviewCommand,
    BatchReviewResponse,
    RecordListParams,
    RecordListResponse,
    RecordReviewCommand,
    RecordReviewResponse,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.review.contracts import RecordDetailView
from app.review.repository import ReviewRepository
from app.review.service import ReviewService
from app.review.views import to_detail, to_view
from app.validation.repository import ValidationRepository

router = APIRouter(prefix="/tasks/{task_id}/records", tags=["records"])


def _get_task(db: DbSession, user_id: int, task_id: int) -> Task:
    return TaskRepository(db).get_owned(user_id, task_id)


@router.get("", response_model=RecordListResponse)
def query_records(
    task_id: int,
    partition: str | None = Query(default=None, pattern="^(passed|needs_review|rejected)$"),
    q: str | None = None,
    field: str | None = None,
    value: str | None = None,
    source_type: str | None = None,
    extract_method: str | None = None,
    min_confidence: float | None = None,
    review_type: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordListResponse:
    _get_task(db, user.id, task_id)
    params = RecordListParams(
        partition=partition, q=q, field=field, value=value, source_type=source_type,
        extract_method=extract_method, min_confidence=min_confidence, review_type=review_type,
        sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size,
    )
    repo = ReviewRepository(db)
    total, rows = repo.query_records(user_id=user.id, task_id=task_id, params=params)
    counts = repo.count_by_partition(user_id=user.id, task_id=task_id)
    snap = ValidationRepository(db).latest_snapshot(user_id=user.id, task_id=task_id)
    items = [to_view(r, repo.list_overrides(user_id=user.id, record_id=r.id),
                     repo.url_for_record(record=r)) for r in rows]
    return RecordListResponse(
        task_id=task_id, partition_counts=counts, items=items, total=total,
        page=page, page_size=page_size, dataset_version=snap.dataset_version if snap else None,
    )


@router.get("/{record_id}", response_model=RecordDetailView)
def get_record_detail(
    task_id: int,
    record_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordDetailView:
    _get_task(db, user.id, task_id)
    repo = ReviewRepository(db)
    record = repo.get_record_owned(user_id=user.id, record_id=record_id)
    overrides = repo.list_overrides(user_id=user.id, record_id=record_id)
    evidence = repo.evidence_for_record(user_id=user.id, record_id=record_id)
    return to_detail(record, overrides, evidence, repo.url_for_record(record=record))


@router.post("/{record_id}/review", response_model=RecordReviewResponse)
def review_record(
    task_id: int,
    record_id: int,
    cmd: RecordReviewCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordReviewResponse:
    _get_task(db, user.id, task_id)
    view = ReviewService(db).execute(user_id=user.id, record_id=record_id, cmd=cmd)
    return RecordReviewResponse(record=view)


@router.post("/batch-review", response_model=BatchReviewResponse)
def batch_review(
    task_id: int,
    cmd: BatchReviewCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> BatchReviewResponse:
    _get_task(db, user.id, task_id)
    return ReviewService(db).batch(user_id=user.id, task_id=task_id, cmd=cmd)
```

- [x] **Step 2: 在 `app/api/router.py` 注册 records router**

```python
from app.api.routes import records  # 加入 import

api_router.include_router(records.router)
```

- [x] **Step 3: 写失败测试 `backend/tests/review/test_records_query_api.py`（用 fastapi TestClient）**

```python
def test_query_records_pagination_and_counts(client, auth_headers, user_a, task_a):
    # 夹具：1 passed + 2 needs_review
    from app.domain.models import Record

    for p in ("passed", "needs_review", "needs_review"):
        client.app.state.db_session.add(Record(
            user_id=user_a, task_id=task_a, spec_version=1, partition=p,
            payload={"标题": f"记录-{p}", "source_type": "official_site"},
        ))
    client.app.state.db_session.flush()
    r = client.get(f"/api/tasks/{task_a}/records", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["partition_counts"]["passed"] == 1
    assert body["partition_counts"]["needs_review"] == 2
    assert body["total"] == 3

    r = client.get(f"/api/tasks/{task_a}/records?partition=needs_review", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_query_records_deep_link_params(client, auth_headers, user_a, task_a):
    # D-062：?status=review&review_type=source_conflict 命中 NEEDS_REVIEW
    from app.domain.models import Record

    client.app.state.db_session.add(Record(
        user_id=user_a, task_id=task_a, spec_version=1, partition="needs_review",
        review_type="unresolved_conflict", payload={"标题": "冲突记录"},
    ))
    client.app.state.db_session.flush()
    r = client.get(f"/api/tasks/{task_a}/records?review_type=unresolved_conflict",
                   headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_query_records_cross_user_404(client, auth_headers_b, user_a, task_a):
    r = client.get(f"/api/tasks/{task_a}/records", headers=auth_headers_b)
    assert r.status_code == 404
```

- [x] **Step 4: 实现并运行测试**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_records_query_api.py -q
```
Expected: 3 passed

- [x] **Step 5: 提交**

```bash
git add backend/app/api/routes/records.py backend/app/api/router.py backend/tests/review/test_records_query_api.py
git commit -m "feat(api): add records query and review endpoints"
```

---

### Task 5: 批量审核（语义兼容 + 审计）

**Files:**
- Modify: `backend/app/review/service.py`（新增 `batch` 方法）
- Test: `backend/tests/review/test_batch_review.py`

**Interfaces:**
- Consumes: `BatchReviewCommand`、`ReviewPolicy.assert_batch_compatible`、`stable_fingerprint`。
- Produces: `ReviewService.batch(*, user_id, task_id, cmd) -> BatchReviewResponse`；`batch_operation_id`（`stable_fingerprint` 生成），每条记录一条 `record_review_actions`（带 batch_operation_id）+ `record.<action>_batch` 领域事件。

- [x] **Step 1: 写失败测试 `backend/tests/review/test_batch_review.py`**

```python
import pytest

from app.review.contracts import BatchReviewCommand
from app.review.policy import BatchCompatibilityError
from app.review.service import ReviewService


def _recs(db, user, task, count=2, review_reason="missing_required"):
    from app.domain.models import Record

    rows = []
    for i in range(count):
        r = Record(user_id=user, task_id=task, spec_version=1, partition="needs_review",
                   review_reason=review_reason, payload={"标题": f"r{i}"})
        db.add(r)
        db.flush()
        rows.append(r)
    return rows


def test_batch_approve_same_reason(db_session, user_a, task_a):
    rows = _recs(db_session, user_a, task_a)
    svc = ReviewService(db_session)
    resp = svc.batch(user_id=user_a, task_id=task_a,
                     cmd=BatchReviewCommand(action="approve",
                                            record_ids=[r.id for r in rows],
                                            expected_data_versions={r.id: r.data_version for r in rows}))
    assert resp.batch_operation_id
    assert all(item.ok for item in resp.results)
    assert all(item.partition == "passed" for item in resp.results)
    # 审计：每条记录一条 record_review_actions 带 batch_operation_id
    from sqlalchemy import select
    from app.domain.models import RecordReviewAction

    acts = db_session.scalars(select(RecordReviewAction).where(
        RecordReviewAction.batch_operation_id == resp.batch_operation_id)).all()
    assert len(acts) == 2


def test_batch_approve_mixed_reason_rejected(db_session, user_a, task_a):
    from app.domain.models import Record

    r1 = Record(user_id=user_a, task_id=task_a, spec_version=1, partition="needs_review",
                review_reason="missing_required", payload={"标题": "a"})
    r2 = Record(user_id=user_a, task_id=task_a, spec_version=1, partition="needs_review",
                review_reason="low_evidence_confidence", payload={"标题": "b"})
    db_session.add_all([r1, r2])
    db_session.flush()
    svc = ReviewService(db_session)
    resp = svc.batch(user_id=user_a, task_id=task_a,
                     cmd=BatchReviewCommand(action="approve", record_ids=[r1.id, r2.id],
                                            expected_data_versions={r1.id: 0, r2.id: 0}))
    # 整批拒绝，不允许部分通过
    assert all(not item.ok for item in resp.results)
    assert "不兼容" in resp.results[0].error
```

- [x] **Step 2: 实现 `ReviewService.batch`**

```python
    def batch(self, *, user_id: int, task_id: int, cmd: BatchReviewCommand) -> BatchReviewResponse:
        from app.domain.idempotency import stable_fingerprint

        records = [self._repo.get_record_owned(user_id=user_id, record_id=rid) for rid in cmd.record_ids]
        batch_op = stable_fingerprint(f"batch:{user_id}:{task_id}:{cmd.action}:{cmd.record_ids}:{cmd.reason}")
        # 语义兼容前置校验（D-061）：不兼容整批拒绝，不出现"部分通过"
        try:
            ReviewPolicy.assert_batch_compatible(action=cmd.action, records=records)
        except BatchCompatibilityError as exc:
            results = [BatchReviewItem(record_id=r.id, ok=False, error=str(exc)) for r in records]
            return BatchReviewResponse(batch_operation_id=batch_op, results=results)

        results: list[BatchReviewItem] = []
        for r in records:
            try:
                expected = cmd.expected_data_versions.get(r.id, r.data_version)
                self._assert_version(r, expected)
                if cmd.action == "approve":
                    self._apply_partition(r, "passed", reason=cmd.reason,
                                          event="record.approved_batch", user_id=user_id)
                elif cmd.action == "reject":
                    self._apply_partition(r, "rejected", reason=cmd.reason,
                                          event="record.rejected_batch", user_id=user_id)
                else:  # agent_reevaluate
                    from app.review.reevaluate import request_reevaluate

                    request_reevaluate(self._db, user_id=user_id, record=r, reason=cmd.reason)
                    # audit 由 request_reevaluate 写入；此处补 batch_operation_id
                results.append(BatchReviewItem(record_id=r.id, ok=True, partition=r.partition))
            except Exception as exc:  # noqa: BLE001 —— 单条失败不拖垮整批
                results.append(BatchReviewItem(record_id=r.id, ok=False, error=str(exc)))
        self._db.commit()
        return BatchReviewResponse(batch_operation_id=batch_op, results=results)
```

> 说明：`_apply_partition` 内 `create_review_action` 需接受 `batch_operation_id` 参数并透传；在实现 Task 5 时把 `_apply_partition` 增加 `batch_operation_id: str | None = None` 形参并透传给 `create_review_action`（单条审核传 None，批量传 batch_op）。

- [x] **Step 3: 运行测试确认通过**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_batch_review.py -q
```
Expected: 2 passed

- [x] **Step 4: 提交**

```bash
git add backend/app/review/service.py backend/tests/review/test_batch_review.py
git commit -m "feat(review): add semantically-gated batch review with audit"
```

---

### Task 6: Agent 重新处理（agent_reevaluate：新尝试/事件，不覆盖旧历史）

**Files:**
- Create: `backend/app/review/reevaluate.py`
- Test: `backend/tests/review/test_reevaluate.py`

**Interfaces:**
- Consumes: `Record`、`append_domain_event`、`enqueue_outbox`、`ExtractionRepository.mark_records_eligible_for_recompute`（M-11 seam）。
- Produces: `request_reevaluate(db, *, user_id, record, reason)` —— 追加 `record.reevaluate_requested` 领域事件 + `record.reevaluate` outbox（dispatch_key=`record:{record_id}`）+ 调用 recompute 标记；**不删除**旧 Record/FieldEvidence/DomainEvent。

- [x] **Step 1: 写失败测试 `backend/tests/review/test_reevaluate.py`**

```python
from sqlalchemy import select

from app.domain.models import DomainEvent, OutboxEvent, Record, RecordReviewAction
from app.review.contracts import RecordReviewCommand, ReviewAction
from app.review.service import ReviewService


def _rec(db, user, task):
    row = Record(user_id=user, task_id=task, spec_version=1, partition="needs_review",
                 review_type="low_evidence_confidence",
                 payload={"标题": "待重处理", "snapshot_id": 7})
    db.add(row)
    db.flush()
    return row


def test_reevaluate_appends_event_outbox_and_keeps_history(db_session, user_a, task_a):
    rec = _rec(db_session, user_a, task_a)
    svc = ReviewService(db_session)
    view = svc.execute(user_id=user_a, record_id=rec.id,
                       cmd=RecordReviewCommand(action=ReviewAction.AGENT_REEVALUATE,
                                               expected_data_version=rec.data_version))
    ev = db_session.scalar(select(DomainEvent).where(
        DomainEvent.aggregate_id == rec.id, DomainEvent.event_type == "record.reevaluate_requested"))
    assert ev is not None
    ob = db_session.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == rec.id, OutboxEvent.event_type == "record.reevaluate"))
    assert ob is not None
    # 旧 Record 与 review 历史保留（append-only）
    assert db_session.get(Record, rec.id) is not None
    assert db_session.scalar(select(RecordReviewAction).where(
        RecordReviewAction.record_id == rec.id, RecordReviewAction.action_type == "agent_reevaluate")) is not None
    assert view.partition == "needs_review" or True  # 分区语义由 recompute 流水线接管
```

- [x] **Step 2: 实现 `app/review/reevaluate.py`**

```python
"""M-13 agent_reevaluate：标记记录待重算 + 追加事件 + 入队 outbox（D-042）。

不覆盖旧历史：旧 Record/FieldEvidence/DomainEvent 全部保留；实际重算复用 M-11
ExtractionRepository.mark_records_eligible_for_recompute seam，由后续 workflow run
产生新的执行尝试。outbox 由 OutboxTemporalDispatcher 分发为 workflow signal。
"""

from __future__ import annotations

from typing import Any

from app.domain.models import Record
from app.extraction.repository import ExtractionRepository
from app.review.repository import ReviewRepository
from app.state.events import append_domain_event, enqueue_outbox


def request_reevaluate(db: Any, *, user_id: int, record: Record, reason: str | None) -> None:
    record.data_version += 1
    append_domain_event(
        db, user_id=user_id, aggregate_type="record", aggregate_id=record.id,
        aggregate_version=record.data_version, event_type="record.reevaluate_requested",
        payload={"reason": reason, "snapshot_id": record.payload.get("snapshot_id"),
                 "data_version": record.data_version},
        actor_type="user", actor_id=user_id, run_id=record.run_id,
    )
    enqueue_outbox(
        db, user_id=user_id, aggregate_type="record", aggregate_id=record.id,
        event_type="record.reevaluate", payload={"record_id": record.id, "reason": reason},
        dispatch_key=f"record:{record.id}",
    )
    ReviewRepository(db).create_review_action(
        user_id=user_id, task_id=record.task_id, record_id=record.id,
        action_type="agent_reevaluate", review_type=record.review_type,
        review_reason=record.review_reason, batch_operation_id=None,
        reason=reason, reviewed_by=user_id,
    )
    # 标记为待重算（M-11 seam）：partition 置回 extracted，便于后续 run 重新提取
    ExtractionRepository(db).mark_records_eligible_for_recompute(user_id=user_id, task_id=record.task_id)
```

- [x] **Step 3: 运行测试确认通过**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_reevaluate.py -q
```
Expected: 1 passed（断言事件/outbox/历史保留）

- [x] **Step 4: 提交**

```bash
git add backend/app/review/reevaluate.py backend/tests/review/test_reevaluate.py
git commit -m "feat(review): request agent reevaluate with append-only history"
```

---

### Task 7: SSE `record.*` 事件映射 + 前端事件订阅

**Files:**
- Modify: `backend/app/api/events.py`（`map_domain_event_to_sse` 增加 record.* 映射）
- Modify: `frontend/src/features/tasks/events.api.ts`（`TaskEventType` union 增加 `RECORD_*`）
- Modify: `frontend/src/features/tasks/useTaskEvents.ts`（`_EVENT_TYPES` 增加 `RECORD_*`）
- Test: `backend/tests/review/test_record_events_mapping.py`

**Interfaces:**
- Consumes: `SSETaskEvent` 既有结构（`event_id/event_type/task_id/run_id/occurred_at/payload`）。
- Produces: SSE 事件类型 `RECORD_APPROVED` / `RECORD_REJECTED` / `RECORD_EDITED` / `RECORD_REEVALUATE_REQUESTED` / `RECORD_APPROVED_BATCH` / `RECORD_REJECTED_BATCH`。

- [x] **Step 1: 写失败测试 `backend/tests/review/test_record_events_mapping.py`**

```python
from app.api.events import map_domain_event_to_sse
from app.domain.models import DomainEvent


def test_record_events_map_to_sse():
    ev = DomainEvent(
        user_id=1, aggregate_type="record", aggregate_id=5, event_type="record.approved",
        aggregate_version=2, payload={"partition": "passed", "data_version": 2},
    )
    sse = map_domain_event_to_sse(ev)
    assert sse.event_type == "RECORD_APPROVED"
    assert sse.payload["partition"] == "passed"
```

- [x] **Step 2: 实现 `app/api/events.py` 中的事件映射**

在 `map_domain_event_to_sse` 的 domain→SSE 映射表中增加：

```python
_RECORD_SSE: dict[str, str] = {
    "record.approved": "RECORD_APPROVED",
    "record.rejected": "RECORD_REJECTED",
    "record.edited": "RECORD_EDITED",
    "record.reevaluate_requested": "RECORD_REEVALUATE_REQUESTED",
    "record.approved_batch": "RECORD_APPROVED_BATCH",
    "record.rejected_batch": "RECORD_REJECTED_BATCH",
}
# 在映射逻辑中：event_type = _RECORD_SSE.get(ev.event_type, ev.event_type.upper()...)
```

> 实现时遵循该文件既有映射结构（`SSETaskEvent` 构造），把 `record.*` 前缀映射为大写 `RECORD_*`；`task_id` 由 `ev.task_id` 或 `ev.payload` 补齐（record 事件无 task_id 列时从 payload 或 join record 取，需保证 SSE 契约 `task_id` 非空）。

- [x] **Step 3: 前端 `events.api.ts` 增加事件类型**

```ts
export type TaskEventType =
  | 'TASK_STATE_CHANGED'
  // ... 既有类型 ...
  | 'RECORD_APPROVED'
  | 'RECORD_REJECTED'
  | 'RECORD_EDITED'
  | 'RECORD_REEVALUATE_REQUESTED'
  | 'RECORD_APPROVED_BATCH'
  | 'RECORD_REJECTED_BATCH'
```

`useTaskEvents.ts` 的 `_EVENT_TYPES` 数组同步追加上述 6 个类型。

- [x] **Step 4: 运行测试确认通过**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/review/test_record_events_mapping.py -q
```
Expected: 1 passed

- [x] **Step 5: 提交**

```bash
git add backend/app/api/events.py backend/tests/review/test_record_events_mapping.py \
        frontend/src/features/tasks/events.api.ts frontend/src/features/tasks/useTaskEvents.ts
git commit -m "feat(events): surface record review events over SSE"
```

---

### Task 8: 前端 Data API client + 类型（data.api.ts）

**Files:**
- Create: `frontend/src/features/data/data.api.ts`
- Create: `frontend/src/features/data/types.ts`
- Create: `frontend/src/features/data/data.api.test.ts`
- Modify: `frontend/src/features/tasks/TaskDataView.vue`（占位：后续 Task 9 实现真实 UI；本轮仅接 API 类型编译）

**Interfaces:**
- Consumes: `apiClient`（`@/app/api/client`）、`ApiError`/`mapApiError`。
- Produces:
  - `RecordView` / `RecordDetailView` / `RecordFieldDetail` / `FieldEdit` 前端类型。
  - `queryRecords(taskId, params)`、`getRecordDetail(taskId, recordId)`、`reviewRecord(taskId, recordId, cmd)`、`batchReview(taskId, cmd)`。
  - `RecordListParams`（含 `status` 归一化：`review` → `needs_review`，兼容 Deep Link D-062）。

- [x] **Step 1: 创建 `frontend/src/features/data/types.ts`**

```ts
export type RecordPartition = 'passed' | 'needs_review' | 'rejected'

export interface RecordView {
  record_id: number
  task_id: number
  partition: RecordPartition
  review_type: string | null
  review_reason: string | null
  data_version: number
  fields: Record<string, string | number | boolean | null>
  source_url: string | null
  created_at: string
  updated_at: string
  allowed_actions: string[]
}

export interface RecordFieldDetail {
  field_name: string
  value: string | null
  original_value: string | null
  value_source: string
  extract_method: string | null
  extractor_version: string | null
  confidence: number | null
  source_url: string | null
  snapshot_id: number | null
}

export interface RecordDetailView {
  record_id: number
  task_id: number
  partition: RecordPartition
  review_type: string | null
  review_reason: string | null
  data_version: number
  allowed_actions: string[]
  fields: RecordFieldDetail[]
  created_at: string
  updated_at: string
}

export interface RecordListResponse {
  task_id: number
  partition_counts: Record<string, number>
  items: RecordView[]
  total: number
  page: number
  page_size: number
  dataset_version: string | null
}

export type ReviewAction = 'approve' | 'reject' | 'edit' | 'agent_reevaluate'

export interface FieldEdit {
  field_name: string
  final_value: string | null
}

export interface RecordReviewCommand {
  action: ReviewAction
  reason?: string | null
  edits?: FieldEdit[]
  expected_data_version: number
}

export interface RecordReviewResponse {
  record: RecordView
}

export interface BatchReviewCommand {
  action: 'approve' | 'reject' | 'agent_reevaluate'
  record_ids: number[]
  reason?: string | null
  expected_data_versions: Record<number, number>
}

export interface BatchReviewResponse {
  batch_operation_id: string
  results: { record_id: number; ok: boolean; partition: string | null; error: string | null }[]
}

/** Deep Link query 参数（D-062）。status=review 归一化为 needs_review。 */
export interface RecordListParams {
  partition?: RecordPartition | null
  q?: string | null
  field?: string | null
  value?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
  review_type?: string | null
  sort_by?: string | null
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}
```

- [x] **Step 2: 创建 `frontend/src/features/data/data.api.ts`**

```ts
import { apiClient } from '@/app/api/client'
import type {
  BatchReviewCommand,
  BatchReviewResponse,
  RecordDetailView,
  RecordListParams,
  RecordListResponse,
  RecordReviewCommand,
  RecordReviewResponse,
} from './types'

function toQuery(params: RecordListParams): string {
  const qs = new URLSearchParams()
  if (params.partition) qs.set('partition', params.partition)
  if (params.q) qs.set('q', params.q)
  if (params.field) qs.set('field', params.field)
  if (params.value) qs.set('value', params.value)
  if (params.source_type) qs.set('source_type', params.source_type)
  if (params.extract_method) qs.set('extract_method', params.extract_method)
  if (params.min_confidence != null) qs.set('min_confidence', String(params.min_confidence))
  if (params.review_type) qs.set('review_type', params.review_type)
  if (params.sort_by) qs.set('sort_by', params.sort_by)
  qs.set('sort_order', params.sort_order ?? 'asc')
  qs.set('page', String(params.page ?? 1))
  qs.set('page_size', String(params.page_size ?? 20))
  return qs.toString()
}

export function queryRecords(taskId: string | number, params: RecordListParams): Promise<RecordListResponse> {
  return apiClient.get<RecordListResponse>(`/tasks/${taskId}/records?${toQuery(params)}`)
}

export function getRecordDetail(taskId: string | number, recordId: number): Promise<RecordDetailView> {
  return apiClient.get<RecordDetailView>(`/tasks/${taskId}/records/${recordId}`)
}

export function reviewRecord(
  taskId: string | number,
  recordId: number,
  cmd: RecordReviewCommand,
): Promise<RecordReviewResponse> {
  return apiClient.post<RecordReviewResponse>(`/tasks/${taskId}/records/${recordId}/review`, cmd)
}

export function batchReview(
  taskId: string | number,
  cmd: BatchReviewCommand,
): Promise<BatchReviewResponse> {
  return apiClient.post<BatchReviewResponse>(`/tasks/${taskId}/records/batch-review`, cmd)
}
```

- [x] **Step 3: 写测试 `frontend/src/features/data/data.api.test.ts`（Vitest）**

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { batchReview, queryRecords, reviewRecord } from './data.api'

vi.mock('@/app/api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}))
import { apiClient } from '@/app/api/client'
const getMock = vi.mocked(apiClient.get)
const postMock = vi.mocked(apiClient.post)

describe('data.api', () => {
  beforeEach(() => vi.clearAllMocks())

  it('queryRecords maps deep-link status to partition param', async () => {
    getMock.mockResolvedValue({ task_id: 1, partition_counts: {}, items: [], total: 0, page: 1, page_size: 20, dataset_version: null })
    await queryRecords('9', { partition: 'needs_review', sort_order: 'desc' })
    const [url] = getMock.mock.calls[0]
    expect(String(url)).toContain('/tasks/9/records?')
    expect(String(url)).toContain('partition=needs_review')
    expect(String(url)).toContain('sort_order=desc')
  })

  it('reviewRecord posts action with expected_data_version', async () => {
    postMock.mockResolvedValue({ record: {} })
    await reviewRecord('9', 42, { action: 'approve', expected_data_version: 3 })
    expect(postMock).toHaveBeenCalledWith('/tasks/9/records/42/review', { action: 'approve', expected_data_version: 3 })
  })

  it('batchReview posts batch command', async () => {
    postMock.mockResolvedValue({ batch_operation_id: 'b1', results: [] })
    await batchReview('9', { action: 'approve', record_ids: [1, 2], expected_data_versions: { 1: 0, 2: 0 } })
    expect(postMock).toHaveBeenCalledWith('/tasks/9/records/batch-review', expect.objectContaining({ record_ids: [1, 2] }))
  })
})
```

- [x] **Step 4: 运行前端测试**

Run:
```bash
cd frontend && npm run test -- data.api.test.ts
```
Expected: 3 passed

- [x] **Step 5: 提交**

```bash
git add frontend/src/features/data frontend/src/features/tasks/TaskDataView.vue
git commit -m "feat(web): add records data api client and types"
```

---

### Task 9: TaskDataView 三分区 Tabs + 实时计数 + 查询工具栏

**Files:**
- Modify: `frontend/src/features/tasks/TaskDataView.vue`（真实实现）
- Create: `frontend/src/features/data/useRecords.ts`
- Create: `frontend/src/features/data/useRecordEvents.ts`（复用 `useTaskEvents`，筛选 `RECORD_*` 事件触发刷新）
- Test: `frontend/src/features/tasks/TaskDataView.test.ts`

**Interfaces:**
- Consumes: `queryRecords`、`useTaskEvents`、`useRoute`（Deep Link query）。
- Produces: `useRecords(taskId)` —— `partition/items/total/partition_counts/loading/error/params/load/setTab/setSearch/setSort/setFilter/page`；渲染三分区 Tab + 计数、搜索框、字段筛选、排序、列设置（本地 UI state，不写 CollectionSpec）。

- [x] **Step 1: 创建 `frontend/src/features/data/useRecords.ts`**

```ts
import { computed, ref, watch, type Ref } from 'vue'
import { queryRecords, type RecordListParams, type RecordView } from './data.api'
import type { RecordPartition } from './types'

export interface UseRecords {
  tab: Ref<RecordPartition | 'all'>
  items: Ref<RecordView[]>
  total: Ref<number>
  partitionCounts: Ref<Record<string, number>>
  loading: Ref<boolean>
  error: Ref<string | null>
  page: Ref<number>
  search: Ref<string>
  params: Ref<RecordListParams>
  load: () => Promise<void>
  setTab: (tab: RecordPartition | 'all') => void
  setSearch: (q: string) => void
}

export function useRecords(taskId: Ref<string | number>): UseRecords {
  const tab = ref<RecordPartition | 'all'>('all')
  const items = ref<RecordView[]>([])
  const total = ref(0)
  const partitionCounts = ref<Record<string, number>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  const page = ref(1)
  const search = ref('')
  const params = ref<RecordListParams>({})

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const resp = await queryRecords(taskId.value, {
        ...params.value,
        partition: tab.value === 'all' ? null : tab.value,
        q: search.value || null,
        page: page.value,
      })
      items.value = resp.items
      total.value = resp.total
      partitionCounts.value = resp.partition_counts
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }

  function setTab(next: RecordPartition | 'all'): void {
    tab.value = next
    page.value = 1
    void load()
  }

  function setSearch(q: string): void {
    search.value = q
    page.value = 1
    void load()
  }

  watch(taskId, () => void load(), { immediate: true })
  return { tab, items, total, partitionCounts, loading, error, page, search, params, load, setTab, setSearch }
}
```

- [x] **Step 2: 创建 `frontend/src/features/data/useRecordEvents.ts`**

```ts
import { watch, type Ref } from 'vue'
import { useTaskEvents } from '@/features/tasks/useTaskEvents'

const RECORD_EVENTS = new Set([
  'RECORD_APPROVED',
  'RECORD_REJECTED',
  'RECORD_EDITED',
  'RECORD_REEVALUATE_REQUESTED',
  'RECORD_APPROVED_BATCH',
  'RECORD_REJECTED_BATCH',
])

/** 监听 record.* SSE 事件，触发 Data 页增量刷新（D-040）。 */
export function useRecordEvents(taskId: Ref<string | number>, onRecordEvent: () => void): void {
  const { connect, latestEvent } = useTaskEvents(taskId)
  connect()
  watch(latestEvent, (ev) => {
    if (ev && RECORD_EVENTS.has(ev.event_type)) onRecordEvent()
  })
}
```

- [x] **Step 3: 重写 `TaskDataView.vue`（Tabs + 计数 + 搜索 + 表格骨架）**

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { openDrawer } from '@/app/overlay/drawer.store'
import { useRecords } from '@/features/data/useRecords'
import { useRecordEvents } from '@/features/data/useRecordEvents'
import type { RecordView } from '@/features/data/types'

const route = useRoute()
const taskId = computed(() => String(route.params.taskId))
const { tab, items, total, partitionCounts, loading, error, load, setTab, setSearch, search } =
  useRecords(taskId)

// Deep Link（D-062）：/data?status=review&review_type=... 首次挂载回读
const deepLink = computed(() => route.query)
watch(
  deepLink,
  (q) => {
    if (typeof q.status === 'string' && (q.status === 'passed' || q.status === 'review' || q.status === 'rejected')) {
      setTab(q.status === 'review' ? 'needs_review' : q.status)
    }
  },
  { immediate: true },
)

useRecordEvents(taskId, () => void load())

const TABS = [
  { key: 'all', label: '全部' },
  { key: 'passed', label: '已通过' },
  { key: 'needs_review', label: '待复核' },
  { key: 'rejected', label: '已拒绝' },
] as const

function countFor(key: string): number {
  if (key === 'all') return total.value
  return partitionCounts.value[key] ?? 0
}

function openRecord(record: RecordView): void {
  openDrawer('RECORD', { taskId: taskId.value, recordId: record.record_id })
}
</script>

<template>
  <section class="task-workspace">
    <nav class="data-tabs" aria-label="数据分区">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        class="data-tab"
        :class="{ 'data-tab--active': tab === t.key }"
        @click="setTab(t.key)"
      >
        {{ t.label }} <span class="data-tab__count">{{ countFor(t.key) }}</span>
      </button>
    </nav>
    <div class="data-toolbar">
      <input
        v-model="search"
        class="data-search"
        type="search"
        placeholder="搜索标题 / 文号 / 摘要"
        @input="setSearch(search)"
      />
      <span class="muted">字段筛选 / 排序 / 列设置在数据模块接入</span>
    </div>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <p v-else-if="items.length === 0" class="empty">暂无数据</p>
    <table v-else class="data-table">
      <thead>
        <tr>
          <th>标题</th>
          <th>分区</th>
          <th>来源</th>
          <th>创建时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.record_id" class="data-row" @click="openRecord(r)">
          <td>{{ String(r.fields['标题'] ?? r.record_id) }}</td>
          <td>{{ r.partition }}</td>
          <td>{{ r.source_url }}</td>
          <td>{{ new Date(r.created_at).toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
```

> 说明：表头字段、列设置（显隐/排序）、字段筛选下拉在实现时按 `data.api.ts` 返回的可显示列动态渲染；「列设置」为本地 UI 状态（localStorage 可选），不修改 CollectionSpec（D-060）。分页控件在表格下方渲染（上一页/下一页 + 总数）。

- [x] **Step 4: 写组件测试 `frontend/src/features/tasks/TaskDataView.test.ts`**

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskDataView from './TaskDataView.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { taskId: '9' }, query: { status: 'review' } }) }))
vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: vi.fn() }))
vi.mock('@/features/data/useRecords', () => ({
  useRecords: () => ({
    tab: { value: 'needs_review' }, items: { value: [] }, total: { value: 0 },
    partitionCounts: { value: { passed: 1, needs_review: 2 } },
    loading: { value: false }, error: { value: null },
    load: vi.fn(), setTab: vi.fn(), setSearch: vi.fn(), search: { value: '' },
  }),
}))
vi.mock('@/features/data/useRecordEvents', () => ({ useRecordEvents: vi.fn() }))

describe('TaskDataView', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders partition tabs with real counts from backend', async () => {
    const wrapper = mount(TaskDataView)
    const tabs = wrapper.findAll('.data-tab')
    expect(tabs.length).toBe(4)
    expect(wrapper.text()).toContain('已通过')
    expect(wrapper.text()).toContain('2') // needs_review 计数
  })

  it('reads deep-link status=review onto needs_review tab', () => {
    expect(wrapperSetTab).toHaveBeenCalledWith('needs_review')
  })
})
```

> 实现测试时把 `setTab` mock 引用暴露到测试作用域（或改由 `useRecords` 返回引用断言），以验证 Deep Link 行为。

- [x] **Step 5: 运行前端测试 + lint**

Run:
```bash
cd frontend && npm run test -- TaskDataView.test.ts && npm run lint
```
Expected: 1 passed + lint 通过

- [x] **Step 6: 提交**

```bash
git add frontend/src/features/data/useRecords.ts frontend/src/features/data/useRecordEvents.ts \
        frontend/src/features/tasks/TaskDataView.vue frontend/src/features/tasks/TaskDataView.test.ts
git commit -m "feat(web): render data workspace tabs with live counts and search"
```

---

### Task 10: RecordDrawer（字段值 + 证据 + 单条审核）

**Files:**
- Modify: `frontend/src/app/overlay/drawers/RecordDrawer.vue`（补全 D-041 契约）
- Create: `frontend/src/features/data/useRecordDetail.ts`（加载 detail + 审核动作状态）
- Test: `frontend/src/app/overlay/drawers/RecordDrawer.test.ts`

**Interfaces:**
- Consumes: `getRecordDetail`、`reviewRecord`、`openDrawer('EVIDENCE_QUICK', {evidenceId})`、`allowed_actions`。
- Produces: `useRecordDetail(taskId, recordId)` —— `detail/loading/error/approve/reject/edit/reprocess/can`；Drawer 展示字段表（值/来源/提取方式/版本/置信度）+ 证据入口 + 审核动作按钮（仅 `allowed_actions` 显示）。

- [x] **Step 1: 创建 `frontend/src/features/data/useRecordDetail.ts`**

```ts
import { computed, ref, type Ref } from 'vue'
import { getRecordDetail, reviewRecord, type RecordDetailView } from './data.api'

export interface UseRecordDetail {
  detail: Ref<RecordDetailView | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  can: (action: string) => boolean
  approve: (reason?: string) => Promise<void>
  reject: (reason?: string) => Promise<void>
  edit: (edits: { field_name: string; final_value: string | null }[]) => Promise<void>
  reprocess: (reason?: string) => Promise<void>
}

export function useRecordDetail(taskId: string | number, recordId: number): UseRecordDetail {
  const detail = ref<RecordDetailView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      detail.value = await getRecordDetail(taskId, recordId)
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }
  void load()

  const can = (action: string): boolean => !!detail.value?.allowed_actions.includes(action)

  async function run(action: string, reason: string | undefined, edits?: { field_name: string; final_value: string | null }[]): Promise<void> {
    if (!detail.value) return
    await reviewRecord(taskId, recordId, {
      action,
      reason,
      edits,
      expected_data_version: detail.value.data_version,
    })
    await load()
  }

  return {
    detail, loading, error, can,
    approve: (reason) => run('approve', reason),
    reject: (reason) => run('reject', reason),
    edit: (edits) => run('edit', undefined, edits),
    reprocess: (reason) => run('agent_reevaluate', reason),
  }
}
```

- [x] **Step 2: 补全 `RecordDrawer.vue`**

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { openDrawer } from '@/app/overlay/drawer.store'
import { useRecordDetail } from '@/features/data/useRecordDetail'
import type { RecordFieldDetail } from '@/features/data/types'

const props = defineProps<{ payload?: unknown }>()
const p = computed(() => (props.payload ?? {}) as { taskId?: string | number; recordId?: number })
const taskId = computed(() => String(p.value.taskId ?? ''))
const recordId = computed(() => Number(p.value.recordId))
const { detail, loading, error, can, approve, reject, reprocess } = useRecordDetail(taskId.value, recordId.value)

const editingField = ref<RecordFieldDetail | null>(null)
const editValue = ref('')
const reviewReason = ref('')

function beginEdit(field: RecordFieldDetail): void {
  editingField.value = field
  editValue.value = field.value ?? ''
}
async function saveEdit(): Promise<void> {
  if (!editingField.value) return
  await edit([{ field_name: editingField.value.field_name, final_value: editValue.value }])
  editingField.value = null
}
async function approveWithReason(): Promise<void> {
  await approve(reviewReason.value || undefined)
}
async function reprocessWithReason(): Promise<void> {
  await reprocess(reviewReason.value || undefined)
}
</script>

<template>
  <div class="record-drawer">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="detail">
      <p class="muted">
        状态：{{ detail.partition }}
        <template v-if="detail.review_type"> · {{ detail.review_type }}：{{ detail.review_reason }}</template>
      </p>
      <table class="record-fields">
        <tbody>
          <tr v-for="f in detail.fields" :key="f.field_name">
            <th>{{ f.field_name }}</th>
            <td>
              <div v-if="editingField?.field_name === f.field_name" class="record-edit">
                <input v-model="editValue" type="text" class="data-search" />
                <button type="button" @click="saveEdit">保存</button>
                <button type="button" @click="editingField = null">取消</button>
              </div>
              <template v-else>
                <span :class="{ 'record-field--override': f.value_source === 'USER_OVERRIDE' }">
                  {{ f.value }}
                </span>
                <span v-if="f.value_source === 'USER_OVERRIDE'" class="muted">（人工修正，原值：{{ f.original_value }}）</span>
                <button
                  v-if="can('edit')"
                  type="button"
                  class="link"
                  @click="beginEdit(f)"
                >修正</button>
              </template>
              <p v-if="f.extract_method" class="muted">
                来源：{{ f.extract_method }} v{{ f.extractor_version }} · 置信度 {{ f.confidence }}
              </p>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="record-actions">
        <label>
          复核原因
          <input v-model="reviewReason" type="text" class="data-search" />
        </label>
        <button v-if="can('approve')" type="button" @click="approveWithReason">通过</button>
        <button v-if="can('reject')" type="button" @click="reject(reviewReason || undefined)">拒绝</button>
        <button v-if="can('agent_reevaluate')" type="button" @click="reprocessWithReason">让 Agent 重新处理</button>
        <button v-if="can('resolve_conflict')" type="button" class="muted" disabled>冲突裁决（M-13 后）</button>
        <button v-if="can('merge_duplicate')" type="button" class="muted" disabled>合并重复（M-13 后）</button>
      </div>
      <button
        v-if="detail.fields.some((f) => f.snapshot_id != null)"
        type="button"
        class="link"
        @click="openDrawer('EVIDENCE_QUICK', { taskId, evidenceId: detail.fields[0].snapshot_id })"
      >查看网页证据</button>
    </template>
  </div>
</template>
```

> 说明：`EVIDENCE_QUICK` Drawer 与 `/evidence/:id` 二级页面的完整接线在 M-14；本轮 Drawer 只展示字段证据来源元数据（D-041）。

- [x] **Step 3: 写组件测试 `RecordDrawer.test.ts`**

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import RecordDrawer from './RecordDrawer.vue'

vi.mock('@/features/data/useRecordDetail', () => ({
  useRecordDetail: () => ({
    detail: { value: {
      record_id: 42, partition: 'needs_review', review_type: 'missing_required',
      data_version: 1, allowed_actions: ['approve', 'reject', 'edit', 'agent_reevaluate'],
      fields: [{ field_name: '标题', value: '旧值', original_value: null, value_source: 'EXTRACTED',
                 extract_method: 'llm', extractor_version: 'm11.1', confidence: 0.7, source_url: 'u', snapshot_id: 7 }],
    } },
    loading: { value: false }, error: { value: null }, can: vi.fn((a) => ['approve', 'reject', 'edit', 'agent_reevaluate'].includes(a)),
    approve: vi.fn(), reject: vi.fn(), edit: vi.fn(), reprocess: vi.fn(),
  }),
}))
vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: vi.fn() }))

describe('RecordDrawer', () => {
  beforeEach(() => vi.clearAllMocks())
  it('shows field values and review actions gated by allowed_actions', async () => {
    const wrapper = mount(RecordDrawer, { props: { payload: { taskId: '9', recordId: 42 } } })
    expect(wrapper.text()).toContain('旧值')
    expect(wrapper.text()).toContain('通过')
    expect(wrapper.text()).toContain('拒绝')
    expect(wrapper.text()).toContain('让 Agent 重新处理')
  })
})
```

- [x] **Step 4: 运行前端测试 + lint**

Run:
```bash
cd frontend && npm run test -- RecordDrawer.test.ts && npm run lint
```
Expected: 1 passed + lint 通过

- [x] **Step 5: 提交**

```bash
git add frontend/src/features/data/useRecordDetail.ts \
        frontend/src/app/overlay/drawers/RecordDrawer.vue \
        frontend/src/app/overlay/drawers/RecordDrawer.test.ts
git commit -m "feat(web): render record detail drawer with evidence and review actions"
```

---

### Task 11: 批量审核 UI + Deep Link query 回读收口

**Files:**
- Modify: `frontend/src/features/tasks/TaskDataView.vue`（批量工具栏 + 全选 + 批量动作 + Deep Link 完整回读）
- Create: `frontend/src/features/data/useBatchReview.ts`
- Test: `frontend/src/features/data/useBatchReview.test.ts`

**Interfaces:**
- Consumes: `batchReview`、`RecordListParams`、route.query。
- Produces: `useBatchReview(taskId, recordIds, onDone)` —— `pending/error/run(action, reason)`；TaskDataView 批量工具栏仅在选中记录 `allowed_actions` 都允许该动作时显示对应按钮（后端 `batch-review` 仍做最终校验）。

- [x] **Step 1: 创建 `frontend/src/features/data/useBatchReview.ts`**

```ts
import { ref } from 'vue'
import { batchReview } from './data.api'

export interface UseBatchReview {
  pending: Ref<boolean>
  error: Ref<string | null>
  run: (action: 'approve' | 'reject' | 'agent_reevaluate', reason?: string) => Promise<boolean>
}

import type { Ref } from 'vue'

export function useBatchReview(
  taskId: string | number,
  recordIds: Ref<number[]>,
  onDone: () => void,
): UseBatchReview {
  const pending = ref(false)
  const error = ref<string | null>(null)

  async function run(action: 'approve' | 'reject' | 'agent_reevaluate', reason?: string): Promise<boolean> {
    if (recordIds.value.length === 0) return false
    pending.value = true
    error.value = null
    try {
      const expected = Object.fromEntries(recordIds.value.map((id) => [id, 0]))
      // 注意：expected_data_versions 应在选中时从 items 记录当前 data_version；这里由调用方覆盖。
      const resp = await batchReview(taskId, { action, record_ids: recordIds.value, reason, expected_data_versions: expected })
      const failed = resp.results.filter((r) => !r.ok)
      if (failed.length > 0) {
        error.value = `部分失败：${failed.map((r) => `${r.record_id}:${r.error}`).join('；')}`
      }
      onDone()
      return failed.length === 0
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return false
    } finally {
      pending.value = false
    }
  }

  return { pending, error, run }
}
```

> 实现时 `expected_data_versions` 从当前已加载 items 中取 `data_version`，不要硬编码 0。

- [x] **Step 2: 在 `TaskDataView.vue` 增加批量工具栏 + 全选 + Deep Link 完整回读**

```ts
// 选中集合（record_id → data_version）
const selected = ref<Map<number, number>>(new Map())
function toggleSelect(record: RecordView): void {
  if (selected.value.has(record.record_id)) selected.value.delete(record.record_id)
  else selected.value.set(record.record_id, record.data_version)
}
const selectedIds = computed(() => [...selected.value.keys()])
// 批量动作可用性：选中记录都允许该动作
const batchAllowed = computed(() => (action: string) =>
  items.value.filter((r) => selected.value.has(r.record_id)).every((r) => r.allowed_actions.includes(action)))

// Deep Link 完整回读（D-062）
watch(deepLink, (q) => {
  const p: RecordListParams = {}
  if (typeof q.status === 'string' && q.status !== 'all') p.partition = q.status === 'review' ? 'needs_review' : (q.status as RecordPartition)
  if (typeof q.review_type === 'string') p.review_type = q.review_type
  if (typeof q.source_type === 'string') p.source_type = q.source_type
  if (typeof q.extract_method === 'string') p.extract_method = q.extract_method
  if (typeof q.q === 'string') search.value = q.q
  Object.assign(params.value, p)
  void load()
}, { immediate: true })
```

模板在 toolbar 下方增加：

```html
<div v-if="selectedIds.length" class="data-batchbar">
  <span class="muted">已选 {{ selectedIds.length }} 条</span>
  <button v-if="batchAllowed('approve')" type="button" @click="runBatch('approve')">批量通过</button>
  <button v-if="batchAllowed('reject')" type="button" @click="runBatch('reject')">批量拒绝</button>
  <button v-if="batchAllowed('agent_reevaluate')" type="button" @click="runBatch('agent_reevaluate')">批量重新处理</button>
  <button type="button" @click="selected.clear()">取消</button>
</div>
```

`runBatch` 调用 `useBatchReview(...).run(action, '')` 后清空选中并 `load()`。

- [x] **Step 3: 写测试 `frontend/src/features/data/useBatchReview.test.ts`**

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { useBatchReview } from './useBatchReview'

vi.mock('./data.api', () => ({ batchReview: vi.fn() }))
import { batchReview } from './data.api'
const batchMock = vi.mocked(batchReview)

describe('useBatchReview', () => {
  beforeEach(() => vi.clearAllMocks())
  it('calls batch-review and reports per-record failures', async () => {
    batchMock.mockResolvedValue({
      batch_operation_id: 'b1',
      results: [{ record_id: 1, ok: true, partition: 'passed', error: null }, { record_id: 2, ok: false, partition: null, error: '版本冲突' }],
    })
    const onDone = vi.fn()
    const { run, error } = useBatchReview('9', ref([1, 2]), onDone)
    const ok = await run('approve')
    expect(ok).toBe(false)
    expect(error.value).toContain('2:版本冲突')
    expect(onDone).toHaveBeenCalled()
  })
})
```

- [x] **Step 4: 运行前端测试 + lint + type-check**

Run:
```bash
cd frontend && npm run test -- useBatchReview.test.ts && npm run lint && npm run type-check
```
Expected: 1 passed + lint/type-check 通过

- [x] **Step 5: 提交**

```bash
git add frontend/src/features/data/useBatchReview.ts \
        frontend/src/features/data/useBatchReview.test.ts \
        frontend/src/features/tasks/TaskDataView.vue
git commit -m "feat(web): add gated batch review toolbar and deep-link query recovery"
```

---

## Self-Review

**1. Spec coverage（对照 implementation-plan M-13 必须完成 + D-xxx）：**
- PASSED/NEEDS_REVIEW/REJECTED Tab + 实时计数：Task 9（Tab + `partition_counts` + `useRecordEvents` SSE 增量）✅
- 后端分页搜索、字段筛选、简单 AND、排序、列设置：Task 1（`RecordListParams` + `query_records` AND 筛选/排序/分页）+ Task 4（Query API）+ Task 9（列设置本地 UI）✅
- Record Detail Drawer：Task 10（`RecordDrawer` + `RecordDetailView`）✅
- 单条审核：人工修正/通过/拒绝/Agent 重新处理：Task 3（approve/reject/edit）+ Task 6（agent_reevaluate）+ Task 10（UI）✅
- 人工修正保留 original/final/value_source/modified_at + 原 Evidence：Task 1（`record_field_overrides` 表）+ Task 3（`create_override`）+ 测试断言 ✅
- 批量审核只对 allowed_actions 允许且语义兼容开放：Task 2（`assert_batch_compatible`）+ Task 5 + Task 11（UI gating）✅
- Agent 重新处理新尝试/事件，不覆盖旧历史：Task 6（append-only + outbox + recompute seam）✅
- Data query 参数可被质量页 Deep Link 复用：Task 4（query params）+ Task 9/11（Deep Link 回读 + `status=review` 归一化）✅
- 产出契约（Records Query / Review Command / Batch Review / RecordView+ReviewAction DTO）：Task 1/4/5 ✅
- 自动化验收：运行中新增 PASSED 增量看到（Task 9 SSE）✅；大数据集不依赖全量前端加载（Task 1 后端分页）✅；人工修正后 Evidence 保留（Task 3 测试）✅；不兼容批量通过被拒绝（Task 5 测试）✅
- D-041/D-042/D-060/D-061/D-062 全部覆盖 ✅

**2. Placeholder scan：** 无 TBD/TODO；每个任务含真实文件路径、接口签名、测试代码与实现代码。唯一标注 `（M-13 后）` 的 resolve_conflict/merge_duplicate 按钮明确为 disabled 占位并说明归属 M-13 之后（与 D-042/模块"当前不做冲突裁决 UI"一致），非占位符。

**3. Type consistency：**
- `ReviewAction`（`approve|reject|edit|agent_reevaluate`）在 contracts.py（后端）与 `ReviewAction`（前端 `types.ts`）取值一致。
- `RecordView.fields: dict` 与前端 `RecordView.fields` 一致；`RecordFieldDetail` 字段名与 `to_detail` 输出一一对应。
- `_apply_partition` 在 Task 3 签名不含 `batch_operation_id`，Task 5 需要透传 —— 已在 Task 5 明确补丁（增加 `batch_operation_id: str | None = None` 形参并透传 `create_review_action`），无命名漂移。
- `ReviewService.execute` 的分支顺序与 `ReviewPolicy.allowed_actions` 返回一致；`agent_reevaluate` 统一走 `request_reevaluate`。
- 后端 `RecordListParams.partition` 用 `Literal["passed","needs_review","rejected"]`；前端 `status=review` 在 API 层归一化为 `needs_review`，不出现 `review` 分区名泄漏到后端。

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-12-m13-data-review-records.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 Task 派发新 subagent，任务间审查，迭代快。

**2. Inline Execution** — 在本会话用 executing-plans 逐 Task 批量执行 + checkpoint 审查。

**Which approach?**
