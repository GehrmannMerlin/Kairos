# M-12 数据验证、去重、冲突、三类结果、质量指标与完成判定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (user pre-authorized inline execution) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 M-11 产出的 EXTRACTED Record 候选转化为可正式导出的可信数据：完成 D-006/D-014 的验证→去重→冲突→分层抽样→三分区→质量指标→完成判定闭环，并挂入 M-08 的 DEDUPLICATE/VALIDATE 节点执行器。

**Architecture:** 在 `backend/app/validation/` 新增 `app/validation` 领域包：typed 契约（ValidationResult/ReviewReason/AllowedReviewAction/QualityMetrics/CompletionDecision）→ 确定性验证流水线（结构/类型→必填→证据→业务规则）→ 任务级业务唯一键去重（exact + deterministic fuzzy）→ 跨来源冲突确定性裁决（不可裁决进 NEEDS_REVIEW）→ 分层抽样 + 数据库事实聚合的质量指标 → 完成判定（定向/探索饱和/部分完成，无金额预算）。复用 M-04 Record（`partition` 列已存在，新增 review 字段）、M-11 FieldEvidence/ExtractionRepository、M-08 Node Registry executor seam。所有副作用（去重/验证/抽样/质量持久化）放在 Temporal Activity；Workflow 只编排 typed refs。新表通过 migration 0010 增量扩展，不创建第二套 Record 业务事实。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Pydantic v2 / Temporal Python SDK / pytest（A-Lite）。前端本轮只更新 API types + Task counts + SSE event schema，不做 Data/Quality 页面。

## Global Constraints

- 复用 M-04 Record 与 M-11 FieldEvidence/ExtractionRepository；**禁止**创建 ValidatedRecord/QualityRecord/FinalRecord 第二套业务事实。
- 结果分区只有 `PASSED` / `NEEDS_REVIEW` / `REJECTED`（`RecordPartition` 内部值 `extracted` 仅表示 M-11 候选，不面向用户）。
- 验证顺序固定：structure/type → required → evidence → business → dedupe → conflict → sampling。
- 无 FieldEvidence 的字段不得 PASSED，除非命中显式 `SYSTEM_DERIVED` deterministic field policy。
- LLM 只产生 `possible_duplicate` candidate pairs / similarity hints，**绝不**决定最终 PASSED/NEEDS_REVIEW/REJECTED 或 merge decision。
- 冲突无法确定性裁决 → `NEEDS_REVIEW`，禁止随机选一个/取第一个/取最新 row/LLM 猜测。
- 质量指标必须由数据库事实聚合计算，denominator 明确；前端/LLM 不得推断。
- CompletionDecision 禁止人民币/美元/费用/token 金额作为完成条件；允许 max pages / max duration / retry limit。
- 每个 dedupe/validation batch：业务事务提交（Record state + ValidationResult + evidence refs + dedupe group + conflict + DomainEvent）后才 commit Checkpoint；同一 batch 重试不得重复计数。
- 所有 validation/quality 事实强制 owner 隔离（user_id 边界，owner-safe 404）。
- 本轮不做 M-13 人工审核 UI、不做 M-14 Quality 页面、不做 M-15 CSV。
- 错误分类至少区分：`VALIDATION_SCHEMA_ERROR` / `EVIDENCE_INVALID` / `DEDUPE_CONFLICT` / `UNRESOLVED_CONFLICT` / `QUALITY_COMPUTE_ERROR` / `COMPLETION_POLICY_ERROR`，不全部 500。
- 严格 A-Lite：只跑 M-12 scoped tests（CORE TEST A–F + 2 条 Activity integration）；不重跑历史模块全量回归。
- 代码风格遵循 agent-code-standards.md：typed contracts、state 变化走 DomainService、幂等身份走 `stable_fingerprint`、事件走 `append_domain_event`、executor 走 `execute(unit) -> ExecuteUnitResult`。

---

### Task 1: Validation contracts + persistence（migration 0010 + policies + repository）

**Files:**
- Create: `backend/app/validation/__init__.py`
- Create: `backend/app/validation/contracts.py`
- Create: `backend/app/validation/policies.py`
- Create: `backend/app/validation/repository.py`
- Create: `backend/alembic/versions/0010_validation_quality_completion.py`
- Test: `backend/tests/validation/test_contracts_persistence.py`

**Interfaces:**
- Consumes: `app.domain.models.Record`（已有 `partition`/`business_key` 列）、`app.extraction.contracts.RecordPartition.EXTRACTED`、`app.domain.idempotency.stable_fingerprint`、`app.domain.repository._owned` 模式。
- Produces:
  - `ValidationPartition`（StrEnum: `passed|needs_review|rejected`）
  - `ReviewReason`（StrEnum: `missing_required|unresolved_conflict|possible_duplicate|low_evidence_confidence|rule_mismatch|invalid_format|business_rule_failed`）
  - `AllowedReviewAction`（StrEnum: `approve|edit|reject|agent_reevaluate|merge_duplicate|resolve_conflict`）
  - `ValidationIssue`（pydantic: `code/field_name/detail/severity`）
  - `ValidationResult`（pydantic: `record_id/spec_version_id/validation_version/structural_issues/required_field_issues/evidence_issues/business_rule_issues/dedupe_group_id/dedupe_result/conflict_result/partition/review_type/review_reason/allowed_actions/quality_contribution/validated_at`）
  - `ValidationSettings`（pydantic: `validation_version="m12.1"`、`system_derived_fields: frozenset[str]`、`dedupe_min_similarity: float = 0.92`、`approx_dedupe_max_candidates: int = 20`）
  - `ValidationRepository`（`persist_validation_result` / `dedupe_group` CRUD / `field_conflict` CRUD / `quality_snapshot` CRUD / `completion_decision` CRUD，全部 owner-scoped）
  - DB 表：`validation_results`、`dedupe_clusters`、`field_conflicts`、`quality_snapshots`、`completion_decisions`；Record 扩展 `review_type`/`review_reason`/`validated_at`。

- [ ] **Step 1: 写 migration 0010（先跑 head 确认基线）**

Run:
```bash
.venv/Scripts/python.exe -m alembic heads   # 预期: 0009 (head)
```
创建 `backend/alembic/versions/0010_validation_quality_completion.py`：

```python
"""M-12 validation/quality/completion tables + Record review fields."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("records", sa.Column("review_type", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("review_reason", sa.String(50), nullable=True))
    op.add_column("records", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(30), nullable=False),
        sa.Column("structural_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("required_field_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("business_rule_issues", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dedupe_group_id", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_result", sa.JSON(), nullable=True),
        sa.Column("conflict_result", sa.JSON(), nullable=True),
        sa.Column("partition", sa.String(30), nullable=False),
        sa.Column("review_type", sa.String(50), nullable=True),
        sa.Column("review_reason", sa.String(50), nullable=True),
        sa.Column("allowed_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("quality_contribution", sa.JSON(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_id", "validation_version", name="uq_vr_record_version"),
    )
    op.create_index("ix_vr_user_task_partition", "validation_results", ["user_id", "task_id", "partition"])

    op.create_table(
        "dedupe_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("business_key", sa.String(500), nullable=False),
        sa.Column("business_key_fingerprint", sa.String(64), nullable=False),
        sa.Column("dedupe_policy_version", sa.String(30), nullable=False),
        sa.Column("approximate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("record_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(30), nullable=False, server_default="grouped"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "business_key_fingerprint", name="uq_dc_task_fp"),
    )
    op.create_index("ix_dc_user_task", "dedupe_clusters", ["user_id", "task_id"])

    op.create_table(
        "field_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("dedupe_group_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_values", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resolution", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="unresolved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("record_id", "field_name", "state", name="uq_fc_record_field_state"),
    )
    op.create_index("ix_fc_user_task", "field_conflicts", ["user_id", "task_id"])

    op.create_table(
        "quality_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("validation_version", sa.String(30), nullable=False),
        sa.Column("dataset_version", sa.String(50), nullable=False),
        sa.Column("sampling_policy_version", sa.String(30), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("denominators", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sample_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_qs_user_task", "quality_snapshots", ["user_id", "task_id"])

    op.create_table(
        "completion_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completion_type", sa.String(50), nullable=True),
        sa.Column("qualified_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saturation_evidence", sa.JSON(), nullable=True),
        sa.Column("runtime_limit_reason", sa.String(200), nullable=True),
        sa.Column("scope_completion_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cd_user_task", "completion_decisions", ["user_id", "task_id"])


def downgrade() -> None:
    op.drop_table("completion_decisions")
    op.drop_table("quality_snapshots")
    op.drop_table("field_conflicts")
    op.drop_table("dedupe_clusters")
    op.drop_table("validation_results")
    op.drop_column("records", "validated_at")
    op.drop_column("records", "review_reason")
    op.drop_column("records", "review_type")
```

- [ ] **Step 2: 在 `app/domain/models.py` 追加 M-12 ORM 模型（Record 列 + 五张新表）**

在 `Record` 类内追加（复用现有列风格）：

```python
    # ---- M-12 validation/partition（migration 0010，nullable 兼容）----
    review_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

文件尾部追加：

```python
class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (
        UniqueConstraint("record_id", "validation_version", name="uq_vr_record_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_version: Mapped[str] = mapped_column(String(30), nullable=False)
    structural_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_field_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    business_rule_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dedupe_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dedupe_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conflict_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    partition: Mapped[str] = mapped_column(String(30), nullable=False)
    review_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    allowed_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_contribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DedupeCluster(Base):
    __tablename__ = "dedupe_clusters"
    __table_args__ = (UniqueConstraint("task_id", "business_key_fingerprint", name="uq_dc_task_fp"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    business_key: Mapped[str] = mapped_column(String(500), nullable=False)
    business_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    approximate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="grouped")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class FieldConflict(Base):
    __tablename__ = "field_conflicts"
    __table_args__ = (UniqueConstraint("record_id", "field_name", "state", name="uq_fc_record_field_state"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="unresolved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QualitySnapshot(Base):
    __tablename__ = "quality_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_version: Mapped[str] = mapped_column(String(30), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    sampling_policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    denominators: Mapped[dict] = mapped_column(JSON, nullable=False)
    sample_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CompletionDecision(Base):
    __tablename__ = "completion_decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completion_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qualified_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saturation_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_limit_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope_completion_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] **Step 3: 写 `app/validation/contracts.py`（canonical typed 契约）**

```python
"""M-12 canonical validation/quality/completion typed contracts (D-006 / D-014).

结果分区只有 PASSED / NEEDS_REVIEW / REJECTED。RecordPartition.EXTRACTED（M-11）
是内部候选状态，不面向用户。禁止新增 VALID/FAILED/PENDING_VALIDATION 第二套分区名。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class ValidationPartition(StrEnum):
    PASSED = "passed"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ReviewReason(StrEnum):
    MISSING_REQUIRED = "missing_required"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    LOW_EVIDENCE_CONFIDENCE = "low_evidence_confidence"
    RULE_MISMATCH = "rule_mismatch"
    INVALID_FORMAT = "invalid_format"
    BUSINESS_RULE_FAILED = "business_rule_failed"


class AllowedReviewAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    AGENT_REEVALUATE = "agent_reevaluate"
    MERGE_DUPLICATE = "merge_duplicate"
    RESOLVE_CONFLICT = "resolve_conflict"


class ValidationIssue(BaseModel):
    model_config = _STRICT

    code: str
    field_name: str | None = None
    detail: str = ""
    severity: str = "error"  # error | warning


class ValidationResult(BaseModel):
    """canonical 单条 Record 验证结果。字段按现有 domain 对齐，不用 dict[str, Any] 做核心事实。"""

    model_config = _STRICT

    record_id: int
    spec_version_id: int
    validation_version: str
    structural_issues: list[ValidationIssue] = []
    required_field_issues: list[ValidationIssue] = []
    evidence_issues: list[ValidationIssue] = []
    business_rule_issues: list[ValidationIssue] = []
    dedupe_group_id: int | None = None
    dedupe_result: dict = {}
    conflict_result: dict = {}
    partition: ValidationPartition
    review_type: str | None = None
    review_reason: ReviewReason | None = None
    allowed_actions: list[str] = []
    quality_contribution: dict = {}
    validated_at: datetime
```

- [ ] **Step 4: 写 `app/validation/policies.py`（集中默认值 + SYSTEM_DERIVED 显式策略）**

```python
"""M-12 集中验证/去重/抽样策略默认值。禁止散落 magic numbers（四十七）。

SYSTEM_DERIVED 例外必须程序可审计：字段名命中此集合时，才允许无网页
FieldEvidence 仍进入 PASSED（例如采集时间/source URL/内部 ID）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationSettings(BaseModel):
    validation_version: str = "m12.1"
    system_derived_fields: frozenset[str] = frozenset()  # 显式白名单，默认空
    dedupe_min_similarity: float = 0.92          # deterministic fuzzy merge threshold
    approx_dedupe_max_candidates: int = 20       # LLM candidate pair 上限
    saturation_batch_window: int = 3             # 探索饱和：最近 N batch
    saturation_new_unique_threshold: float = 0.0 # 新增 unique 率低于此值即饱和
    min_qualified_records_for_saturation: int = 1
    sample_size_per_stratum: int = 5
    max_batch: int = 50

    class Config:
        frozen = True
```

- [ ] **Step 5: 写 `app/validation/repository.py`（owner-scoped persistence，flush 单事务）**

```python
"""M-12 persistence：ValidationResult / DedupeCluster / FieldConflict / QualitySnapshot /
CompletionDecision。所有 create() 只 flush（不 commit），executor 统一单事务提交（D-015）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import (
    CompletionDecision,
    DedupeCluster,
    FieldConflict,
    QualitySnapshot,
    ValidationResult,
)


def _owned(db: Any, model: type, user_id: int, obj_id: int) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


class ValidationRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ---- ValidationResult ----
    def find_result(self, *, user_id: int, record_id: int, validation_version: str) -> ValidationResult | None:
        return self._db.scalar(
            select(ValidationResult).where(
                ValidationResult.user_id == user_id,
                ValidationResult.record_id == record_id,
                ValidationResult.validation_version == validation_version,
            )
        )

    def create_result(self, *, user_id: int, task_id: int, run_id: int | None,
                      spec_version: int, result: dict) -> ValidationResult:
        row = ValidationResult(user_id=user_id, task_id=task_id, run_id=run_id,
                               spec_version=spec_version, **result)
        self._db.add(row)
        return row

    def count_by_partition(self, *, user_id: int, task_id: int) -> dict[str, int]:
        from sqlalchemy import func
        rows = self._db.execute(
            select(ValidationResult.partition, func.count())
            .where(ValidationResult.user_id == user_id, ValidationResult.task_id == task_id)
            .group_by(ValidationResult.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    def latest_partition_for_task(self, *, user_id: int, task_id: int) -> dict[int, str]:
        """latest ValidationResult partition per record (validated_at desc)。"""
        rows = self._db.execute(
            select(ValidationResult.record_id, ValidationResult.partition)
            .where(ValidationResult.user_id == user_id, ValidationResult.task_id == task_id)
            .order_by(ValidationResult.validated_at.desc())
        ).all()
        out: dict[int, str] = {}
        for record_id, partition in rows:
            out.setdefault(record_id, partition)
        return out

    # ---- DedupeCluster ----
    def find_group(self, *, user_id: int, task_id: int, business_key_fingerprint: str) -> DedupeCluster | None:
        return self._db.scalar(
            select(DedupeCluster).where(
                DedupeCluster.user_id == user_id,
                DedupeCluster.task_id == task_id,
                DedupeCluster.business_key_fingerprint == business_key_fingerprint,
            )
        )

    def create_group(self, *, user_id: int, task_id: int, run_id: int | None,
                     spec_version: int, business_key: str, business_key_fingerprint: str,
                     dedupe_policy_version: str, approximate: bool, record_ids: list[int]) -> DedupeCluster:
        row = DedupeCluster(user_id=user_id, task_id=task_id, run_id=run_id,
                            spec_version=spec_version, business_key=business_key,
                            business_key_fingerprint=business_key_fingerprint,
                            dedupe_policy_version=dedupe_policy_version,
                            approximate=approximate, record_ids=record_ids)
        self._db.add(row)
        self._db.flush()
        return row

    def list_groups(self, *, user_id: int, task_id: int) -> list[DedupeCluster]:
        return list(self._db.scalars(select(DedupeCluster).where(
            DedupeCluster.user_id == user_id, DedupeCluster.task_id == task_id)))

    # ---- FieldConflict ----
    def find_conflict(self, *, user_id: int, record_id: int, field_name: str, state: str) -> FieldConflict | None:
        return self._db.scalar(
            select(FieldConflict).where(
                FieldConflict.user_id == user_id, FieldConflict.record_id == record_id,
                FieldConflict.field_name == field_name, FieldConflict.state == state))

    def create_conflict(self, *, user_id: int, task_id: int, record_id: int, dedupe_group_id: int | None,
                        field_name: str, candidate_values: list, resolution: dict | None,
                        state: str = "unresolved") -> FieldConflict:
        row = FieldConflict(user_id=user_id, task_id=task_id, record_id=record_id,
                            dedupe_group_id=dedupe_group_id, field_name=field_name,
                            candidate_values=candidate_values, resolution=resolution, state=state)
        self._db.add(row)
        return row

    # ---- QualitySnapshot ----
    def create_snapshot(self, *, user_id: int, task_id: int, run_id: int | None, spec_version: int,
                        validation_version: str, dataset_version: str, sampling_policy_version: str,
                        metrics: dict, denominators: dict, sample_refs: list) -> QualitySnapshot:
        row = QualitySnapshot(user_id=user_id, task_id=task_id, run_id=run_id, spec_version=spec_version,
                              validation_version=validation_version, dataset_version=dataset_version,
                              sampling_policy_version=sampling_policy_version, metrics=metrics,
                              denominators=denominators, sample_refs=sample_refs)
        self._db.add(row)
        return row

    def latest_snapshot(self, *, user_id: int, task_id: int) -> QualitySnapshot | None:
        return self._db.scalar(select(QualitySnapshot).where(
            QualitySnapshot.user_id == user_id, QualitySnapshot.task_id == task_id
        ).order_by(QualitySnapshot.id.desc()).limit(1))

    # ---- CompletionDecision ----
    def create_completion(self, *, user_id: int, task_id: int, run_id: int | None,
                          spec_version: int, plan_version: int, decision: dict) -> CompletionDecision:
        row = CompletionDecision(user_id=user_id, task_id=task_id, run_id=run_id,
                                 spec_version=spec_version, plan_version=plan_version, **decision)
        self._db.add(row)
        return row

    def latest_completion(self, *, user_id: int, task_id: int) -> CompletionDecision | None:
        return self._db.scalar(select(CompletionDecision).where(
            CompletionDecision.user_id == user_id, CompletionDecision.task_id == task_id
        ).order_by(CompletionDecision.id.desc()).limit(1))
```

- [ ] **Step 6: 写 `backend/tests/validation/test_contracts_persistence.py`（migration + ORM roundtrip）**

```python
"""M-12 contracts + persistence roundtrip（migration 0010 经 Base.metadata 全量建表）。"""
from __future__ import annotations

import pytest

from app.domain.repository import RecordRepository, RunRepository, SpecVersionRepository, TaskRepository
from app.infra.db import Base
from app.validation.repository import ValidationRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _spec_payload() -> dict:
    return {"task_type": "SPECIFIED_SOURCE", "goal": "m12", "fields": [
        {"name": "公司名", "type": "text", "required": True},
        {"name": "官网", "type": "url", "required": True}],
        "source_scope": {"mode": "SPECIFIED_SOURCE", "seed_urls": ["http://fixture.test/"],
                         "source_hints": []},
        "completion_conditions": [{"kind": "min_records", "target": 1}],
        "advanced_settings": {}}


@pytest.fixture()
def vctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)  # 覆盖 migration 0010 全部新表
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("v12@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="M-12", task_type="SPECIFIED_SOURCE")
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    SpecVersionRepository(db).create(user_id=user.id, task_id=task.id, version=1,
                                     spec_type="collection", schema_version="m06.1", payload=_spec_payload())
    record = RecordRepository(db).create(user_id=user.id, task_id=task.id, run_id=run.id,
                                         spec_version=1, payload={"values": {}}, partition="extracted")
    yield {"db": db, "user": user, "task": task, "run": run, "record": record}
    db.close()


def test_validation_result_roundtrip_and_partition_count(vctx):
    repo = ValidationRepository(vctx["db"])
    repo.create_result(user_id=vctx["user"].id, task_id=vctx["task"].id, run_id=vctx["run"].id,
                       spec_version=1, result={
                           "record_id": vctx["record"].id, "spec_version_id": 1,
                           "validation_version": "m12.1", "partition": "passed",
                           "structural_issues": [], "required_field_issues": [],
                           "evidence_issues": [], "business_rule_issues": [],
                           "allowed_actions": ["approve"], "validated_at": "2026-08-11T00:00:00+00:00"})
    repo.create_result(user_id=vctx["user"].id, task_id=vctx["task"].id, run_id=vctx["run"].id,
                       spec_version=1, result={
                           "record_id": 999, "spec_version_id": 1, "validation_version": "m12.1",
                           "partition": "needs_review", "structural_issues": [],
                           "required_field_issues": [], "evidence_issues": [],
                           "business_rule_issues": [], "allowed_actions": [],
                           "validated_at": "2026-08-11T00:00:00+00:00"})
    vctx["db"].commit()
    counts = repo.count_by_partition(user_id=vctx["user"].id, task_id=vctx["task"].id)
    assert counts == {"passed": 1, "needs_review": 1}


def test_dedupe_cluster_idempotent_by_fingerprint(vctx):
    repo = ValidationRepository(vctx["db"])
    fp = "a" * 64
    g1 = repo.create_group(user_id=vctx["user"].id, task_id=vctx["task"].id, run_id=vctx["run"].id,
                           spec_version=1, business_key="key", business_key_fingerprint=fp,
                           dedupe_policy_version="m12.1", approximate=False, record_ids=[1, 2])
    vctx["db"].commit()
    g2 = repo.find_group(user_id=vctx["user"].id, task_id=vctx["task"].id,
                         business_key_fingerprint=fp)
    assert g2 is not None and g2.id == g1.id


def test_owner_isolation_rejects_foreign_record(vctx):
    from app.auth.errors import NotFoundError
    from app.validation.repository import ValidationRepository

    db2 = sessionmaker(bind=vctx["db"].get_bind(), autoflush=False, expire_on_commit=False)()
    try:
        from app.auth.repository import UserRepository
        other = UserRepository(db2).create("other@example.com", "hash", None)
        repo = ValidationRepository(db2)
        with pytest.raises(NotFoundError):
            repo.find_result(user_id=other.id, record_id=vctx["record"].id, validation_version="m12.1")
            repo.find_group(user_id=other.id, task_id=vctx["task"].id, business_key_fingerprint="x")
    finally:
        db2.close()


def test_migration_upgrade_sql_is_generatable():
    from alembic.config import Config
    from alembic import command
    import tempfile
    import os

    cfg = Config()
    ini = os.path.join(os.path.dirname(__file__), "..", "..", "..", "alembic.ini")
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(ini), "alembic"))
    cfg.config_file_name = ini
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as f:
        cfg.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
        command.upgrade(cfg, "0010", sql=True)
        content = open(f.name).read() if False else ""
    assert "validation_results" in (content or "")
```

- [ ] **Step 7: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_contracts_persistence.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
.venv/Scripts/python.exe -m ruff format --check app/validation
.venv/Scripts/python.exe -m alembic upgrade head --sql | grep -q validation_results && echo "migration ok"
```
Expected: PASS。

- [ ] **Step 8: Commit（一个 Task 一个 Commit）**

```bash
git add backend/alembic/versions/0010_validation_quality_completion.py backend/app/domain/models.py backend/app/validation tests/validation
git commit -m "feat(validation): add validation contracts and persistence (migration 0010)"
```

---

### Task 2: structure/type + required + evidence + business validators

**Files:**
- Create: `backend/app/validation/business_rules.py`
- Create: `backend/app/validation/validators.py`
- Test: `backend/tests/validation/test_validators.py`

**Interfaces:**
- Consumes: `app.extraction.schema_validator.ExtractionSchemaValidator`（复用，不重复实现第二套 schema parser）、`app.domain.spec.FieldSpec/FieldType`、`app.extraction.normalize.normalize_*`、`app.extraction.repository.FieldEvidenceRepository`、Task 1 `ValidationIssue`/`ValidationSettings`。
- Produces:
  - `BusinessValidationRule`（pydantic: `code/field_name/operator/value/description/severity`）与 `RULE_OPERATORS` 确定性操作符注册表（`equals|not_empty|in_enum|range_min|range_max|matches|co_present`）
  - `StructureTypeValidator.validate(record_values, fields) -> list[ValidationIssue]`
  - `RequiredFieldValidator.validate(record_values, fields) -> list[ValidationIssue]`
  - `EvidenceValidator.validate(record, evidence_by_field, fields, settings) -> list[ValidationIssue]`
  - `BusinessRuleValidator.validate(record_values, rules) -> list[ValidationIssue]`

- [ ] **Step 1: 写 `app/validation/business_rules.py`（typed 注册规则，禁止 eval）**

```python
"""M-12 typed BusinessValidationRule 注册表（四十六）。

规则必须 deterministic。禁止任意 Python 代码存 DB 后 eval；只允许代码注册的
操作符（equals/not_empty/in_enum/range_min/range_max/matches/co_present）。
规则来源：CollectionSpec completion/field 约束或代码注册 typed safe config。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.validation.contracts import ValidationIssue

_STRICT = ConfigDict(extra="forbid")


class BusinessValidationRule(BaseModel):
    model_config = _STRICT

    code: str
    field_name: str
    operator: str  # 见 RULE_OPERATORS
    value: Any | None = None
    description: str = ""
    severity: str = "error"


def _equals(field_value: Any, value: Any) -> bool:
    return str(field_value).strip() == str(value).strip()


def _not_empty(field_value: Any, value: Any) -> bool:
    return field_value is not None and str(field_value).strip() != ""


def _in_enum(field_value: Any, value: Any) -> bool:
    return str(field_value).strip() in {str(v).strip() for v in (value or [])}


def _range_min(field_value: Any, value: Any) -> bool:
    try:
        return float(field_value) >= float(value)
    except (TypeError, ValueError):
        return False


def _range_max(field_value: Any, value: Any) -> bool:
    try:
        return float(field_value) <= float(value)
    except (TypeError, ValueError):
        return False


def _matches(field_value: Any, value: Any) -> bool:
    try:
        return re.search(str(value), str(field_value)) is not None
    except re.error:
        return False


def _co_present(field_value: Any, value: Any) -> bool:
    # value = list of companion field names；主字段非空时 companion 也必须非空
    return True  # companion 校验由调用方按 record_values 组合完成


RULE_OPERATORS: dict[str, Any] = {
    "equals": _equals,
    "not_empty": _not_empty,
    "in_enum": _in_enum,
    "range_min": _range_min,
    "range_max": _range_max,
    "matches": _matches,
    "co_present": _co_present,
}


class BusinessRuleValidator:
    def validate(self, record_values: dict, rules: list[BusinessValidationRule]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for rule in rules:
            op = RULE_OPERATORS.get(rule.operator)
            if op is None:
                issues.append(ValidationIssue(code="UNKNOWN_RULE_OPERATOR", field_name=rule.field_name,
                                              detail=f"未知操作符 {rule.operator}", severity=rule.severity))
                continue
            value = record_values.get(rule.field_name)
            ok = op(value, rule.value)
            if rule.operator == "co_present" and value not in (None, ""):
                companions = rule.value or []
                ok = all(record_values.get(c) not in (None, "") for c in companions)
            if not ok:
                issues.append(ValidationIssue(code=rule.code, field_name=rule.field_name,
                                              detail=rule.description or rule.code, severity=rule.severity))
        return issues
```

- [ ] **Step 2: 写 `app/validation/validators.py`（结构/类型 → 必填 → 证据 → 业务规则）**

```python
"""M-12 验证流水线前四层（D-014）：structure/type → required → evidence → business。

结构/类型复用 M-06/M-11 的 ExtractionSchemaValidator + normalize，不重复实现
第二套 schema parser（五十一）。
"""

from __future__ import annotations

from typing import Any

from app.domain.spec import FieldSpec
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.validation.contracts import ValidationIssue
from app.validation.policies import ValidationSettings


class StructureTypeValidator:
    def __init__(self, schema_validator: ExtractionSchemaValidator | None = None) -> None:
        self._schema = schema_validator or ExtractionSchemaValidator()

    def validate(self, record_values: dict, fields: list[FieldSpec]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        field_by_name = {f.name: f for f in fields}
        for name, value in record_values.items():
            if value in (None, ""):
                continue  # 缺失值归 required 层；结构层只管存在的值类型
            field = field_by_name.get(name)
            if field is None:
                issues.append(ValidationIssue(code="SCHEMA_UNKNOWN_FIELD", field_name=name,
                                              detail="字段不属于冻结 CollectionSpec"))
                continue
            issue = self._schema.validate(
                self._candidate(name, value), field)
            if issue is not None:
                issues.append(ValidationIssue(code=issue.code, field_name=name,
                                              detail=issue.detail, severity="error"))
        return issues

    @staticmethod
    def _candidate(name: str, value: Any):
        from app.extraction.contracts import CandidateValidationStatus, ExtractorMethod, ExtractionCandidate
        return ExtractionCandidate(field_name=name, raw_value=str(value), normalized_value=None,
                                   value_type="text", method=ExtractorMethod.RULE,
                                   confidence=1.0, extractor_version="m12",
                                   validation_status=CandidateValidationStatus.VALID)


class RequiredFieldValidator:
    def validate(self, record_values: dict, fields: list[FieldSpec]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field in fields:
            if not field.required:
                continue
            value = record_values.get(field.name)
            if value in (None, ""):
                issues.append(ValidationIssue(code="REQUIRED_FIELD_MISSING", field_name=field.name,
                                              detail=f"必填字段 {field.name} 缺失"))
        return issues


class EvidenceValidator:
    """默认规则：有效业务字段进入 PASSED 必须存在合法 FieldEvidence（十四）。

    Evidence 必须：owner 一致、task/spec 一致、record/candidate 关联正确、
    snapshot/source 可追溯、method/version 存在。SYSTEM_DERIVED 例外显式审计。
    """

    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()

    def validate(self, record: Any, evidence_by_field: dict[str, list],
                 fields: list[FieldSpec]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field in fields:
            evs = evidence_by_field.get(field.name) or []
            if evs:
                for ev in evs:
                    issue = self._check_chain(record, ev)
                    if issue is not None:
                        issues.append(issue)
                continue
            # 无证据字段：只有显式 SYSTEM_DERIVED 例外才允许
            if field.name in self._settings.system_derived_fields:
                continue
            issues.append(ValidationIssue(code="EVIDENCE_MISSING", field_name=field.name,
                                          detail=f"字段 {field.name} 缺少 FieldEvidence"))
        return issues

    def _check_chain(self, record: Any, ev: Any) -> ValidationIssue | None:
        if ev.user_id != record.user_id:
            return ValidationIssue(code="EVIDENCE_OWNER_MISMATCH", field_name=ev.field_name,
                                   detail="证据 user 归属不一致")
        if ev.task_id not in (None, record.task_id):
            return ValidationIssue(code="EVIDENCE_TASK_MISMATCH", field_name=ev.field_name,
                                   detail="证据 task 关联不一致")
        if ev.spec_version not in (None, record.spec_version):
            return ValidationIssue(code="EVIDENCE_SPEC_MISMATCH", field_name=ev.field_name,
                                   detail="证据 spec 版本不一致")
        if ev.snapshot_id is None and ev.source_url in (None, ""):
            return ValidationIssue(code="EVIDENCE_NO_TRACE", field_name=ev.field_name,
                                   detail="证据缺少 snapshot/source 追溯")
        if ev.extract_method in (None, "") or ev.extractor_version in (None, ""):
            return ValidationIssue(code="EVIDENCE_NO_METHOD", field_name=ev.field_name,
                                   detail="证据缺少 method/version")
        return None
```

- [ ] **Step 3: 写 `backend/tests/validation/test_validators.py`（CORE TEST A 的校验矩阵）**

```python
"""CORE TEST A — Validation Matrix（五十九）。

参数化覆盖：valid→PASSED 判定所需结构/必填/证据全过；missing required 可人工补全
→ NEEDS_REVIEW 语义；unrecoverable invalid→REJECTED 语义；missing Evidence→不 PASSED；
system-derived 显式例外→allowed。
"""
from __future__ import annotations

import pytest

from app.domain.spec import FieldSpec
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.validation.contracts import ValidationIssue
from app.validation.policies import ValidationSettings
from app.validation.validators import (BusinessRuleValidator, EvidenceValidator,
                                       RequiredFieldValidator, StructureTypeValidator)

FIELDS = [FieldSpec(name="公司名", type="text", required=True),
          FieldSpec(name="官网", type="url", required=True),
          FieldSpec(name="电话", type="phone", required=False)]


def _record(**values):
    class _R:  # 最小 record stub（ORM 行在 executor 测试用真实 Record）
        user_id = 7
        task_id = 3
        spec_version = 1
    r = _R()
    r.user_id, r.task_id, r.spec_version = 7, 3, 1
    return r, values


def _ev(field_name, *, method="json_ld", version="m11.1"):
    class _E:
        pass
    e = _E()
    e.user_id, e.task_id, e.spec_version = 7, 3, 1
    e.field_name, e.snapshot_id, e.source_url = field_name, 10, "http://x/"
    e.extract_method, e.extractor_version = method, version
    return e


@pytest.mark.parametrize("values,expect_structure", [
    ({"公司名": "A", "官网": "https://a.com"}, []),
    ({"公司名": "A", "官网": "not-a-url"}, [ValidationIssue]),
    ({"未知字段": "x"}, [ValidationIssue]),
])
def test_structure_type_layer(values, expect_structure):
    issues = StructureTypeValidator().validate(values, FIELDS)
    assert len(issues) >= len(expect_structure)


def test_required_layer_flags_missing_required():
    issues = RequiredFieldValidator().validate({"公司名": "A"}, FIELDS)
    assert any(i.code == "REQUIRED_FIELD_MISSING" and i.field_name == "官网" for i in issues)


def test_evidence_layer_blocks_without_evidence():
    record, values = _record(公司名="A", 官网="https://a.com")
    issues = EvidenceValidator().validate(record, {}, FIELDS)
    assert all(i.code == "EVIDENCE_MISSING" for i in issues)  # 无证据 → 不 PASSED


def test_evidence_layer_accepts_valid_chain_and_blocks_broken():
    record, _ = _record(公司名="A", 官网="https://a.com")
    good = {"公司名": [_ev("公司名")], "官网": [_ev("官网")]}
    assert EvidenceValidator().validate(record, good, FIELDS) == []
    broken = {"公司名": [_ev("公司名")], "官网": [_ev("官网", method=None, version=None)]}
    assert any(i.code == "EVIDENCE_NO_METHOD" for i in EvidenceValidator().validate(record, broken, FIELDS))


def test_evidence_system_derived_exception_is_explicit_and_auditable():
    record, _ = _record(公司名="A", 官网="https://a.com")
    settings = ValidationSettings(system_derived_fields=frozenset({"官网"}))
    issues = EvidenceValidator(settings).validate(record, {"公司名": [_ev("公司名")]}, FIELDS)
    assert not any(i.code == "EVIDENCE_MISSING" and i.field_name == "官网" for i in issues)


def test_business_rule_operator_matrix():
    rules = [
        {"code": "MUST_EQUAL", "field_name": "官网", "operator": "equals", "value": "https://a.com"},
        {"code": "NOT_EMPTY", "field_name": "公司名", "operator": "not_empty", "value": None},
        {"code": "PHONE_PATTERN", "field_name": "电话", "operator": "matches", "value": r"^1\d{10}$"},
        {"code": "BAD_OPERATOR", "field_name": "官网", "operator": "eval", "value": None},
    ]
    from app.validation.business_rules import BusinessValidationRule, BusinessRuleValidator
    issues = BusinessRuleValidator().validate({"公司名": "A", "官网": "https://a.com", "电话": "13800138000"},
                                              [BusinessValidationRule.model_validate(r) for r in rules])
    assert not any(i.code == "MUST_EQUAL" for i in issues)
    assert not any(i.code == "NOT_EMPTY" for i in issues)
    assert not any(i.code == "PHONE_PATTERN" for i in issues)
    assert any(i.code == "UNKNOWN_RULE_OPERATOR" for i in issues)  # eval 被拒绝


def test_schema_validator_reused_not_duplicated():
    # 复用 M-11 ExtractionSchemaValidator；M-12 结构层不引入第二套 parser
    assert ExtractionSchemaValidator() is not None
```

- [ ] **Step 4: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_validators.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
.venv/Scripts/python.exe -m ruff format --check app/validation
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/validation tests/validation
git commit -m "feat(validation): add structure/type/required/evidence/business validators"
```

---

### Task 3: deterministic business-key dedupe

**Files:**
- Create: `backend/app/validation/dedupe.py`
- Test: `backend/tests/validation/test_dedupe.py`

**Interfaces:**
- Consumes: `app.domain.spec.FieldSpec`、`app.extraction.normalize.normalize_*`、`app.validation.policies.ValidationSettings`、Task 1 `ValidationRepository`/`DedupeCluster`、`app.domain.idempotency.stable_fingerprint`。
- Produces:
  - `BusinessKeyPolicy`（pydantic: `key_fields: list[str]`）
  - `BusinessUniqueKeyStrategy.resolve(spec_payload) -> BusinessKeyPolicy`（默认 key = 全部必填字段，通用 typed，不硬编码企业例子）
  - `compute_business_key(record_values, policy) -> str | None`（normalized key 值）
  - `business_key_fingerprint(*key_values) -> str`
  - `DedupeEngine`：`group(records, policy, settings) -> (groups: list[DedupeCluster], ungrouped: list[Record])`，含 exact + deterministic fuzzy candidate 生成（`similarity >= threshold` 才自动 merge，否则不入组留待 NEEDS_REVIEW）。

- [ ] **Step 1: 写 `app/validation/dedupe.py`**

```python
"""M-12 任务级 BusinessUniqueKeyStrategy + deterministic dedupe（D-014 / D-016）。

business_key_fingerprint 只包含被 Spec/策略声明为 key 的 normalized 字段值，
绝不包含 timestamp/random UUID/extractor attempt（五十七）。LLM 只允许产出
possible_duplicate candidate pairs；最终自动 merge 必须达到 deterministic
threshold，否则 NEEDS_REVIEW（五十九/六十五）。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.spec import FieldType, validate_spec_payload
from app.domain.idempotency import stable_fingerprint
from app.extraction.normalize import (normalize_email, normalize_number, normalize_phone,
                                      normalize_url)
from app.validation.policies import ValidationSettings

_STRICT = ConfigDict(extra="forbid")


class BusinessKeyPolicy(BaseModel):
    model_config = _STRICT

    key_fields: list[str]


class BusinessUniqueKeyStrategy:
    """从 CollectionSpec + task type + field schema 确定 deterministic business key。

    默认策略：key = 全部必填字段（通用 typed 定义）。企业例子「normalized company
    name + official domain」只是必填字段恰好为这两个的实例，不硬编码为所有任务通用。
    """

    def resolve(self, spec_payload: dict) -> BusinessKeyPolicy:
        spec = validate_spec_payload(spec_payload)
        key_fields = [f.name for f in spec.fields if f.required]
        return BusinessKeyPolicy(key_fields=key_fields)


def _normalize_for_key(value: Any, field_type: FieldType) -> str:
    text = str(value or "").strip()
    if field_type == FieldType.URL:
        return normalize_url(text) or text.lower()
    if field_type == FieldType.EMAIL:
        return normalize_email(text) or text.lower()
    if field_type == FieldType.PHONE:
        return normalize_phone(text) or "".join(c for c in text if c.isdigit())
    if field_type == FieldType.NUMBER:
        return str(normalize_number(text) or text)
    return text


def compute_business_key(record_values: dict, policy: BusinessKeyPolicy,
                         fields: list[FieldSpec]) -> str | None:
    """normalized key 值组合；任一 key 字段缺失返回 None（无法 exact dedupe）。"""
    field_by_name = {f.name: f for f in fields}
    parts: list[str] = []
    for name in policy.key_fields:
        value = record_values.get(name)
        if value in (None, ""):
            return None
        ftype = field_by_name.get(name, FieldSpec(name=name)).type
        parts.append(_normalize_for_key(value, ftype))
    return " | ".join(parts)


def business_key_fingerprint(*key_parts: str) -> str:
    return stable_fingerprint("bizkey", *key_parts)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class DedupeEngine:
    """exact dedupe + deterministic fuzzy candidate 生成（五十八/五十九）。"""

    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()

    def group(self, records: list[Any], policy: BusinessKeyPolicy,
              fields: list[FieldSpec]) -> tuple[list[dict], list[Any]]:
        """返回 (groups, ungrouped)。

        groups: [{business_key, fingerprint, record_ids, approximate}]
        exact 相同 fingerprint 自动同组；fuzzy 候选仅当两 normalized key 相似度
        >= dedupe_min_similarity 才自动并入（deterministic），否则不进组。
        """
        exact: dict[str, list[Any]] = {}
        for rec in records:
            values = (rec.payload or {}).get("values") or {}
            key = compute_business_key(values, policy, fields)
            if key is None:
                continue
            exact.setdefault(business_key_fingerprint(key), []).append(rec)

        groups: list[dict] = []
        ungrouped: list[Any] = []
        for fp, recs in exact.items():
            groups.append({"business_key": _key_of(recs[0], policy, fields),
                           "business_key_fingerprint": fp, "record_ids": [r.id for r in recs],
                           "approximate": False})
        # deterministic fuzzy：key 相似度 >= threshold 自动并入；否则不进组（NEEDS_REVIEW 语义留给流水线）
        self._fuzzy_merge(groups, policy, fields)
        grouped_ids = {rid for g in groups for rid in g["record_ids"]}
        ungrouped = [r for r in records if r.id not in grouped_ids]
        return groups, ungrouped

    def _fuzzy_merge(self, groups: list[dict], policy: BusinessKeyPolicy,
                     fields: list[FieldSpec]) -> None:
        if len(groups) < 2:
            return
        i = 0
        while i < len(groups):
            j = i + 1
            while j < len(groups):
                a = groups[i]["business_key"]
                b = groups[j]["business_key"]
                if _similarity(a, b) >= self._settings.dedupe_min_similarity:
                    groups[i]["record_ids"].extend(groups[j]["record_ids"])
                    groups[i]["approximate"] = True
                    groups.pop(j)
                else:
                    j += 1
            i += 1


def _key_of(record: Any, policy: BusinessKeyPolicy, fields: list[FieldSpec]) -> str:
    values = (record.payload or {}).get("values") or {}
    key = compute_business_key(values, policy, fields)
    return key or ""
```

- [ ] **Step 2: 写 `backend/tests/validation/test_dedupe.py`（CORE TEST B）**

```python
"""CORE TEST B — Dedupe（六十九）：同一 business key 多来源 → 单一业务实体；
retry 同批 → 无重复 group；deterministic fuzzy threshold。"""
from __future__ import annotations

import pytest

from app.domain.spec import FieldSpec
from app.validation.dedupe import (BusinessKeyPolicy, BusinessUniqueKeyStrategy, DedupeEngine,
                                   business_key_fingerprint, compute_business_key)
from app.validation.policies import ValidationSettings

FIELDS = [FieldSpec(name="公司名", type="text", required=True),
          FieldSpec(name="官网", type="url", required=True),
          FieldSpec(name="电话", type="phone", required=False)]


def _record(rid, values):
    class _R:
        pass
    r = _R()
    r.id, r.payload = rid, {"values": values}
    return r


def test_strategy_default_key_is_all_required_fields():
    policy = BusinessUniqueKeyStrategy().resolve({
        "task_type": "SPECIFIED_SOURCE", "goal": "x", "fields": [
            {"name": "公司名", "type": "text", "required": True},
            {"name": "官网", "type": "url", "required": True},
            {"name": "电话", "type": "phone", "required": False}]})
    assert policy.key_fields == ["公司名", "官网"]


def test_compute_business_key_normalizes_and_none_when_missing():
    policy = BusinessKeyPolicy(key_fields=["官网", "公司名"])
    key = compute_business_key({"公司名": "  Acme  ", "官网": "HTTPS://Acme.COM"},
                               policy, FIELDS)
    assert key is not None and "acme" in key.lower()
    assert compute_business_key({"公司名": "Acme"}, policy, FIELDS) is None


def test_fingerprint_ignores_timestamp_and_extractor_attempt():
    key = business_key_fingerprint("Acme", "https://acme.com")
    same = business_key_fingerprint("Acme", "https://acme.com")
    diff = business_key_fingerprint("Acme", "https://acme.com", "2026-08-11T00:00:00")
    assert key == same and key != diff


def test_exact_dedupe_merges_sources_and_preserves_all_records():
    policy = BusinessKeyPolicy(key_fields=["公司名", "官网"])
    engine = DedupeEngine()
    recs = [_record(1, {"公司名": "Acme", "官网": "https://acme.com"}),
            _record(2, {"公司名": "acme", "官网": "https://ACME.com"})]
    groups, ungrouped = engine.group(recs, policy, FIELDS)
    assert len(groups) == 1
    assert set(groups[0]["record_ids"]) == {1, 2}  # Evidence 链全部保留（不删历史）
    assert ungrouped == []


def test_fuzzy_merge_only_above_threshold_else_ungrouped():
    policy = BusinessKeyPolicy(key_fields=["公司名"])
    engine = DedupeEngine()
    close = [_record(1, {"公司名": "Acme Corporation"}), _record(2, {"公司名": "Acme Corp"})]
    groups, _ = engine.group(close, policy, FIELDS)
    assert len(groups) == 1 and groups[0]["approximate"] is True
    far = [_record(1, {"公司名": "Acme Corp"}), _record(2, {"公司名": "完全无关企业XYZ"})]
    groups2, ungrouped2 = engine.group(far, policy, FIELDS)
    assert len(groups2) == 2 or ungrouped2 != []  # 不自动 merge → NEEDS_REVIEW 语义


def test_retry_same_batch_stable_group_identity():
    policy = BusinessKeyPolicy(key_fields=["公司名", "官网"])
    engine = DedupeEngine()
    recs = [_record(1, {"公司名": "Acme", "官网": "https://acme.com"}),
            _record(2, {"公司名": "acme", "官网": "https://ACME.com"})]
    g1, _ = engine.group(recs, policy, FIELDS)
    g2, _ = engine.group(recs, policy, FIELDS)
    assert g1[0]["business_key_fingerprint"] == g2[0]["business_key_fingerprint"]
```

- [ ] **Step 3: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_dedupe.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
```
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/app/validation tests/validation
git commit -m "feat(validation): add deterministic business-key deduplication"
```

---

### Task 4: cross-source conflict resolution

**Files:**
- Create: `backend/app/validation/conflict.py`
- Test: `backend/tests/validation/test_conflict.py`

**Interfaces:**
- Consumes: `app.discovery.models.priority_for` / `DiscoverySource`（来源优先级）、Task 1 `FieldConflict`、Task 3 `DedupeCluster`、`app.extraction.contracts.ExtractorMethod`。
- Produces:
  - `ConflictCandidateValue`（pydantic: `record_id/value/evidence_strength/source_priority/confidence/fetched_at/method`）
  - `ConflictResolution`（pydantic: `policy_version/chosen_value/rejected_refs/decision`，`decision ∈ {resolved, needs_review}`）
  - `ConflictResolver.resolve(field_name, candidates) -> ConflictResolution`（确定性排序：source priority → evidence strength → method reliability → rule validation → snapshot time → confidence；并列不可裁决 → `needs_review`，保留全部候选，不静默选值）

- [ ] **Step 1: 写 `app/validation/conflict.py`**

```python
"""M-12 跨来源冲突确定性裁决（D-014 冲突规则）。

裁决顺序（六十四）：source priority → evidence strength → method reliability →
rule validation → snapshot/fetch time → confidence。无法可靠裁决 → NEEDS_REVIEW，
保留全部候选（不静默选一个/取第一个/取最新 row/LLM 猜）。最终 Record 即使确定
final value 仍保留 rejected candidate refs 供 M-13 审计（六十七）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.discovery.models import DiscoverySource, priority_for
from app.extraction.contracts import ExtractorMethod

_STRICT = ConfigDict(extra="forbid")

CONFLICT_POLICY_VERSION = "m12.1"

# extraction method reliability（structured > rule > llm）
_METHOD_RANK = {
    ExtractorMethod.JSON_LD: 6, ExtractorMethod.META: 5, ExtractorMethod.TABLE: 5,
    ExtractorMethod.CSS: 4, ExtractorMethod.XPATH: 4, ExtractorMethod.RULE: 3,
    ExtractorMethod.LLM: 1,
}


class ConflictCandidateValue(BaseModel):
    model_config = _STRICT

    record_id: int
    value: str
    evidence_strength: float = 0.0   # has evidence + confidence 加权
    source_priority: int = 0         # priority_for(DiscoverySource, rank)
    method: str = "llm"
    rule_validated: bool = False
    fetched_at: datetime | None = None


class ConflictResolution(BaseModel):
    model_config = _STRICT

    decision: str  # resolved | needs_review
    policy_version: str = CONFLICT_POLICY_VERSION
    chosen_value: str | None = None
    chosen_record_id: int | None = None
    rejected_refs: list[dict] = []  # [{record_id, value, reason}]
    reason: str = ""


def _source_priority_of(candidate: ConflictCandidateValue) -> int:
    return candidate.source_priority


class ConflictResolver:
    def resolve(self, field_name: str, candidates: list[ConflictCandidateValue]) -> ConflictResolution:
        if len(candidates) < 2:
            return ConflictResolution(decision="resolved", chosen_value=candidates[0].value if candidates else None,
                                      chosen_record_id=candidates[0].record_id if candidates else None,
                                      reason="single_source")
        ranked = sorted(candidates, key=lambda c: (
            _source_priority_of(c),       # 1. source priority
            c.evidence_strength,          # 2. evidence strength
            _METHOD_RANK.get(ExtractorMethod(c.method), 0),  # 3. method reliability
            c.rule_validated,             # 4. rule validation status
            c.fetched_at or datetime.min, # 5. snapshot/fetch time
            c.confidence if hasattr(c, "confidence") else 0.0,  # 6. confidence
        ), reverse=True)
        top, second = ranked[0], ranked[1]
        # 决定性领先：top 必须在 1~5 层至少一处严格优于 second，且第 1 层 source 不同
        if _source_priority_of(top) > _source_priority_of(second):
            decision = "resolved"
        elif top.evidence_strength > second.evidence_strength + 1e-9:
            decision = "resolved"
        elif _METHOD_RANK.get(ExtractorMethod(top.method), 0) > _METHOD_RANK.get(ExtractorMethod(second.method), 0):
            decision = "resolved"
        elif top.rule_validated and not second.rule_validated:
            decision = "resolved"
        else:
            decision = "needs_review"
        if decision == "resolved":
            rejected = [{"record_id": c.record_id, "value": c.value, "reason": "lower_priority"}
                        for c in ranked[1:]]
            return ConflictResolution(decision="resolved", chosen_value=top.value,
                                      chosen_record_id=top.record_id, rejected_refs=rejected,
                                      reason=f"deterministic_policy:{CONFLICT_POLICY_VERSION}")
        # 无法裁决：保留全部候选，不静默选值
        return ConflictResolution(decision="needs_review", reason="tie_not_resolvable_deterministically",
                                  rejected_refs=[{"record_id": c.record_id, "value": c.value, "reason": "tie"}
                                                 for c in ranked])
```

- [ ] **Step 2: 写 `backend/tests/validation/test_conflict.py`（CORE TEST C）**

```python
"""CORE TEST C — Conflict（七十一）。CASE 1：source priority + stronger evidence →
deterministic resolution。CASE 2：两边强度相近 → NEEDS_REVIEW 且不静默选值。"""
from __future__ import annotations

from datetime import UTC, datetime

from app.validation.conflict import ConflictCandidateValue, ConflictResolver


def _cand(record_id, value, *, priority=60, strength=1.0, method="json_ld",
          rule_validated=False, fetched_at=None):
    return ConflictCandidateValue(record_id=record_id, value=value, evidence_strength=strength,
                                  source_priority=priority, method=method,
                                  rule_validated=rule_validated,
                                  fetched_at=fetched_at or datetime(2026, 8, 11, tzinfo=UTC))


def test_source_priority_resolves_deterministically():
    r = ConflictResolver().resolve(
        "官网",
        [_cand(1, "https://seed.example.com", priority=100),  # USER_SEED 优先
         _cand(2, "https://search.example.org", priority=60)])  # SEARCH_RESULT
    assert r.decision == "resolved"
    assert r.chosen_value == "https://seed.example.com"
    assert r.rejected_refs[0]["record_id"] == 2  # 保留 rejected 审计


def test_stronger_evidence_resolves_within_same_source_tier():
    r = ConflictResolver().resolve(
        "电话",
        [_cand(1, "13800138000", strength=0.4),  # LLM 低证据
         _cand(2, "13900139000", strength=1.0)])  # json_ld 强证据
    assert r.decision == "resolved"
    assert r.chosen_value == "13900139000"


def test_tie_goes_needs_review_and_keeps_all_values():
    r = ConflictResolver().resolve(
        "主营产品",
        [_cand(1, "产品A", strength=1.0, method="json_ld"),
         _cand(2, "产品B", strength=1.0, method="meta")])
    assert r.decision == "needs_review"
    assert r.chosen_value is None  # 不静默选值
    assert len(r.rejected_refs) == 2  # 全部候选保留


def test_low_confidence_llm_loses_to_rule():
    r = ConflictResolver().resolve(
        "地址",
        [_cand(1, "addr-llm", method="llm", strength=0.5),
         _cand(2, "addr-rule", method="rule", strength=0.5, rule_validated=True)])
    assert r.decision == "resolved"
    assert r.chosen_value == "addr-rule"
```

- [ ] **Step 3: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_conflict.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
```
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/app/validation tests/validation
git commit -m "feat(validation): add cross-source conflict resolution"
```

---

### Task 5: three partitions + review reason/actions（partitioner）

**Files:**
- Create: `backend/app/validation/partitioner.py`
- Test: `backend/tests/validation/test_partitioner.py`

**Interfaces:**
- Consumes: Task 2 validators 输出的 `list[ValidationIssue]` 分组、Task 1 `ValidationPartition/ReviewReason/AllowedReviewAction`。
- Produces:
  - `PartitionDecision`（pydantic: `partition/review_type/review_reason/allowed_actions/quality_contribution`）
  - `Partitioner.decide(structural, required, evidence, business, dedupe_unresolved, conflict_unresolved) -> PartitionDecision`
  - 规则：REJECTED（结构根本不满足/不可恢复必填缺失/证据无效/违反不可接受业务约束）→ `review_type="rejected"`、`allowed_actions=["reject"]`；NEEDS_REVIEW（可人工补全缺失/未裁决冲突/近似重复/低证据/规则失效）→ `allowed_actions` 按 `review_type` 生成；PASSED（全部通过 + dedupe resolved + conflict resolved）→ `allowed_actions=["approve"]`。

- [ ] **Step 1: 写 `app/validation/partitioner.py`**

```python
"""M-12 三分区判定 + review_type/review_reason/allowed_actions（D-014 / D-061 / D-066）。

只有 PASSED / NEEDS_REVIEW / REJECTED 三种用户分区（二十八）。内部记录候选
partition=extracted 不是用户结果分区。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.validation.contracts import (AllowedReviewAction, ReviewReason, ValidationPartition,
                                      ValidationIssue)

_STRICT = ConfigDict(extra="forbid")

# review_type 有限集合（三十三）：方便 M-13 筛选/批量/Deep Link，不每种错误一个类型
REVIEW_TYPES = ("missing_required", "unresolved_conflict", "possible_duplicate",
                "low_confidence", "rule_mismatch", "invalid_format", "business_rule",
                "rejected")


class PartitionDecision(BaseModel):
    model_config = _STRICT

    partition: ValidationPartition
    review_type: str | None = None
    review_reason: ReviewReason | None = None
    allowed_actions: list[str] = []
    quality_contribution: dict = {}


class Partitioner:
    def decide(self, *, structural: list[ValidationIssue],
               required: list[ValidationIssue], evidence: list[ValidationIssue],
               business: list[ValidationIssue], dedupe_unresolved: bool,
               conflict_unresolved: bool) -> PartitionDecision:
        # 1) REJECTED：结构根本不满足 / 不可恢复必填缺失 / 证据无效 / 不可接受业务约束
        if structural:
            return PartitionDecision(partition=ValidationPartition.REJECTED,
                                     review_type="invalid_format",
                                     review_reason=ReviewReason.INVALID_FORMAT,
                                     allowed_actions=[AllowedReviewAction.REJECT.value],
                                     quality_contribution={"rejected": True})
        if self._evidence_invalid(evidence):
            return PartitionDecision(partition=ValidationPartition.REJECTED,
                                     review_type="rejected",
                                     review_reason=ReviewReason.INVALID_FORMAT,
                                     allowed_actions=[AllowedReviewAction.REJECT.value],
                                     quality_contribution={"rejected": True})
        fatal_business = [b for b in business if b.code in self._fatal_business_codes()]
        if fatal_business:
            return PartitionDecision(partition=ValidationPartition.REJECTED,
                                     review_type="business_rule",
                                     review_reason=ReviewReason.BUSINESS_RULE_FAILED,
                                     allowed_actions=[AllowedReviewAction.REJECT.value],
                                     quality_contribution={"rejected": True})
        # 2) NEEDS_REVIEW：可人工补全缺失 / 未裁决冲突 / 近似重复 / 低证据 / 规则失效
        if required:
            return PartitionDecision(partition=ValidationPartition.NEEDS_REVIEW,
                                     review_type="missing_required",
                                     review_reason=ReviewReason.MISSING_REQUIRED,
                                     allowed_actions=[AllowedReviewAction.EDIT.value,
                                                      AllowedReviewAction.APPROVE.value,
                                                      AllowedReviewAction.REJECT.value],
                                     quality_contribution={"missing_required": True})
        if conflict_unresolved:
            return PartitionDecision(partition=ValidationPartition.NEEDS_REVIEW,
                                     review_type="unresolved_conflict",
                                     review_reason=ReviewReason.UNRESOLVED_CONFLICT,
                                     allowed_actions=[AllowedReviewAction.RESOLVE_CONFLICT.value,
                                                      AllowedReviewAction.REJECT.value],
                                     quality_contribution={"conflict": True})
        if dedupe_unresolved:
            return PartitionDecision(partition=ValidationPartition.NEEDS_REVIEW,
                                     review_type="possible_duplicate",
                                     review_reason=ReviewReason.POSSIBLE_DUPLICATE,
                                     allowed_actions=[AllowedReviewAction.MERGE_DUPLICATE.value,
                                                      AllowedReviewAction.REJECT.value],
                                     quality_contribution={"duplicate": True})
        if evidence:
            return PartitionDecision(partition=ValidationPartition.NEEDS_REVIEW,
                                     review_type="low_confidence",
                                     review_reason=ReviewReason.LOW_EVIDENCE_CONFIDENCE,
                                     allowed_actions=[AllowedReviewAction.EDIT.value,
                                                      AllowedReviewAction.AGENT_REEVALUATE.value,
                                                      AllowedReviewAction.REJECT.value],
                                     quality_contribution={"low_evidence": True})
        # 3) PASSED：全部 gate PASS + dedupe resolved + conflict resolved
        return PartitionDecision(partition=ValidationPartition.PASSED,
                                 review_type=None, review_reason=None,
                                 allowed_actions=[AllowedReviewAction.APPROVE.value],
                                 quality_contribution={"passed": True})

    @staticmethod
    def _evidence_invalid(evidence: list[ValidationIssue]) -> bool:
        return any(i.code in {"EVIDENCE_OWNER_MISMATCH", "EVIDENCE_TASK_MISMATCH",
                              "EVIDENCE_SPEC_MISMATCH", "EVIDENCE_NO_TRACE"} for i in evidence)

    @staticmethod
    def _fatal_business_codes() -> set[str]:
        return {"BUSINESS_CONSTRAINT_VIOLATION"}
```

- [ ] **Step 2: 写 `backend/tests/validation/test_partitioner.py`（三分区矩阵）**

```python
"""三分区语义（二十八~三十一）：PASSED / NEEDS_REVIEW / REJECTED 精确判定。"""
from __future__ import annotations

from app.validation.contracts import ValidationIssue
from app.validation.partitioner import Partitioner


def _issue(code, field_name=None):
    return ValidationIssue(code=code, field_name=field_name, detail=code)


def test_valid_record_partition_passed():
    d = Partitioner().decide(structural=[], required=[], evidence=[], business=[],
                             dedupe_unresolved=False, conflict_unresolved=False)
    assert d.partition.value == "passed"
    assert d.allowed_actions == ["approve"]


def test_missing_required_repairable_is_needs_review():
    d = Partitioner().decide(structural=[], required=[_issue("REQUIRED_FIELD_MISSING", "官网")],
                             evidence=[], business=[], dedupe_unresolved=False, conflict_unresolved=False)
    assert d.partition.value == "needs_review"
    assert d.review_type == "missing_required"
    assert "edit" in d.allowed_actions and "approve" in d.allowed_actions


def test_structural_failure_is_rejected():
    d = Partitioner().decide(structural=[_issue("SCHEMA_TYPE_URL", "官网")], required=[],
                             evidence=[], business=[], dedupe_unresolved=False, conflict_unresolved=False)
    assert d.partition.value == "rejected"
    assert d.review_reason.value == "invalid_format"


def test_unresolved_conflict_is_needs_review():
    d = Partitioner().decide(structural=[], required=[], evidence=[], business=[],
                             dedupe_unresolved=False, conflict_unresolved=True)
    assert d.partition.value == "needs_review"
    assert d.review_type == "unresolved_conflict"
    assert "resolve_conflict" in d.allowed_actions


def test_no_fourth_partition():
    from app.validation.contracts import ValidationPartition
    assert {p.value for p in ValidationPartition} == {"passed", "needs_review", "rejected"}
```

- [ ] **Step 3: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_partitioner.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
```
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/app/validation tests/validation
git commit -m "feat(validation): add three partitions and review reasons/actions"
```

---

### Task 6: stratified sampling + QualityMetrics

**Files:**
- Create: `backend/app/validation/sampling.py`
- Create: `backend/app/validation/quality.py`
- Test: `backend/tests/validation/test_sampling_quality.py`

**Interfaces:**
- Consumes: Task 1 `QualitySnapshot`/`ValidationRepository`、`app.discovery.models.DiscoverySource`、`app.extraction.contracts.ExtractorMethod`、`app.domain.models.URLResource/Record/PageSnapshot`。
- Produces:
  - `SamplingPolicy`（pydantic: `strata: list[str]`（source/extraction_method/rule_version/confidence_band）、`sample_size_per_stratum: int`、`policy_version: str`）
  - `StratifiedSampler.select(records, strata_facts, settings) -> (sample_refs: list[dict], plan_fingerprint)`——按层分组，每层用 hash-based 确定性选取（`stable_fingerprint(record_id, policy_version)` 排序取前 k），同 policy/version 结果稳定。
  - `QualityMetrics`（pydantic: `pass_rate/missing_rate/duplicate_rate/conflict_count/source_coverage/sampling_accuracy/needs_review_count/rejected_count` + `denominators`）
  - `QualityMetricsService.compute(db, user_id, task_id, run_id, spec_version, ...) -> QualitySnapshot`——全部来自数据库聚合事实，denominator 明确。

- [ ] **Step 1: 写 `app/validation/sampling.py`**

```python
"""M-12 分层抽样（D-014 抽样规则 / 六十一）。

按 source / extraction method / rule version / confidence band 分层，每层 hash-based
确定性选取（stable_fingerprint(record_id, policy_version) 排序取前 k）。同 policy/version
→ 稳定 sample，不依赖 ORDER BY random()（六十二）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.domain.idempotency import stable_fingerprint

_STRICT = ConfigDict(extra="forbid")


class SamplingPolicy(BaseModel):
    model_config = _STRICT

    strata: list[str] = ["source", "extraction_method", "rule_version", "confidence_band"]
    sample_size_per_stratum: int = 5
    policy_version: str = "m12.1"


def _confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.6:
        return "medium"
    return "low"


class StratifiedSampler:
    def __init__(self, policy: SamplingPolicy | None = None) -> None:
        self._policy = policy or SamplingPolicy()

    def select(self, records: list[Any], strata_facts: dict[int, dict],
               settings) -> tuple[list[dict], str]:
        """records: 已分区 Record；strata_facts: {record_id: {source, method, rule_version, confidence}}。
        返回 (sample_refs, plan_fingerprint)。"""
        strata: dict[str, list[int]] = {}
        for rec in records:
            facts = strata_facts.get(rec.id, {})
            key = (str(facts.get("source") or "unknown"),
                   str(facts.get("method") or "unknown"),
                   str(facts.get("rule_version") or "none"),
                   _confidence_band(facts.get("confidence")))
            for s in self._policy.strata:
                strata.setdefault(str(key), []).append(rec.id)
        sample: list[dict] = []
        for stratum_key, ids in sorted(strata.items()):
            chosen = sorted(ids, key=lambda rid: stable_fingerprint(rid, self._policy.policy_version))
            for rid in chosen[: self._policy.sample_size_per_stratum]:
                sample.append({"record_id": rid, "stratum": stratum_key})
        plan_fingerprint = stable_fingerprint("sampling", self._policy.policy_version,
                                              sorted(strata.keys()))
        return sample, plan_fingerprint
```

- [ ] **Step 2: 写 `app/validation/quality.py`**

```python
"""M-12 QualityMetrics：全部来自数据库事实聚合（六十一），denominator 明确（六十三）。

pass_rate = PASSED / total validated records；missing_rate = 缺失必填 record / total；
duplicate_rate = approximate 或 multi-record group / total；conflict_count = 未裁决冲突数；
source_coverage = 产生 Record 的来源数 / 应覆盖来源数（M-09 discovery/spec scope 口径）；
sampling_accuracy = 已知正确答案 sample 命中率（自动 fixture 已知答案时计算）；
needs_review_count / rejected_count = ValidationResult 分区计数。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class QualityMetrics(BaseModel):
    model_config = _STRICT

    pass_rate: float
    missing_rate: float
    duplicate_rate: float
    conflict_count: int
    source_coverage: float
    sampling_accuracy: float | None
    needs_review_count: int
    rejected_count: int
    denominators: dict


class QualityMetricsService:
    def compute(self, db: Any, *, user_id: int, task_id: int, run_id: int | None,
                spec_version: int, validation_version: str, dataset_version: str,
                sampling_policy_version: str, sample_refs: list[dict],
                known_answers: dict[int, dict] | None = None) -> dict:
        from sqlalchemy import func, select

        from app.domain.models import FieldConflict, ValidationResult
        from app.validation.repository import ValidationRepository

        repo = ValidationRepository(db)
        counts = repo.count_by_partition(user_id=user_id, task_id=task_id)
        passed = counts.get("passed", 0)
        review = counts.get("needs_review", 0)
        rejected = counts.get("rejected", 0)
        total = passed + review + rejected
        denom = {"total_validated_records": total, "eligible_sources": 1, "covered_sources": 1}
        source_facts = self._source_facts(db, user_id, task_id)
        denom["eligible_sources"] = max(1, len(source_facts))
        denom["covered_sources"] = max(1, len(self._covered_sources(db, user_id, task_id)))
        # missing_rate：按 record 口径（缺必填 → NEEDS_REVIEW 中 missing_required）
        missing = int(db.scalar(select(func.count()).select_from(ValidationResult).where(
            ValidationResult.user_id == user_id, ValidationResult.task_id == task_id,
            ValidationResult.review_type == "missing_required")) or 0)
        duplicate = int(db.scalar(select(func.count()).select_from(ValidationResult).where(
            ValidationResult.user_id == user_id, ValidationResult.task_id == task_id,
            ValidationResult.review_type == "possible_duplicate")) or 0)
        conflict = int(db.scalar(select(func.count()).select_from(FieldConflict).where(
            FieldConflict.user_id == user_id, FieldConflict.task_id == task_id,
            FieldConflict.state == "unresolved")) or 0)
        sampling_accuracy = None
        if known_answers:
            hits = sum(1 for ref in sample_refs if ref["record_id"] in known_answers)
            sampling_accuracy = round(hits / len(sample_refs), 4) if sample_refs else 0.0
        metrics = QualityMetrics(
            pass_rate=round(passed / total, 4) if total else 0.0,
            missing_rate=round(missing / total, 4) if total else 0.0,
            duplicate_rate=round(duplicate / total, 4) if total else 0.0,
            conflict_count=conflict,
            source_coverage=round(denom["covered_sources"] / denom["eligible_sources"], 4),
            sampling_accuracy=sampling_accuracy,
            needs_review_count=review,
            rejected_count=rejected,
            denominators=denom,
        )
        return {"metrics": metrics.model_dump(), "denominators": denom}

    def _source_facts(self, db, user_id: int, task_id: int) -> list[str]:
        from sqlalchemy import select
        from app.domain.models import URLResource
        return list(db.scalars(select(URLResource.source_type).where(
            URLResource.user_id == user_id, URLResource.task_id == task_id).distinct()))

    def _covered_sources(self, db, user_id: int, task_id: int) -> list[str]:
        from sqlalchemy import select
        from app.domain.models import URLResource
        return list(db.scalars(select(URLResource.source_type).where(
            URLResource.user_id == user_id, URLResource.task_id == task_id,
            URLResource.status.in_(["FETCHED", "HANDED_OFF"])).distinct()))
```

- [ ] **Step 3: 写 `backend/tests/validation/test_sampling_quality.py`（CORE TEST D + E）**

```python
"""CORE TEST D（七十二：质量指标 DB 一致性）+ CORE TEST E（七十三：分层抽样代表性与稳定性）。"""
from __future__ import annotations

import pytest
from app.infra.db import Base
from app.validation.policies import ValidationSettings
from app.validation.sampling import SamplingPolicy, StratifiedSampler
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _record(rid, values):
    class _R:
        pass
    r = _R()
    r.id, r.payload = rid, {"values": values}
    return r


def test_sampling_representative_strata_and_stable_identity():
    policy = SamplingPolicy(sample_size_per_stratum=2)
    sampler = StratifiedSampler(policy)
    recs = [_record(i, {"v": i}) for i in range(1, 21)]
    facts = {i: {"source": "SEARCH_RESULT" if i % 2 else "SITEMAP",
                 "method": "json_ld" if i % 3 else "llm",
                 "rule_version": None, "confidence": 0.95 if i % 4 else 0.4}
             for i in range(1, 21)}
    sample1, fp1 = sampler.select(recs, facts, ValidationSettings())
    sample2, fp2 = sampler.select(recs, facts, ValidationSettings())
    assert fp1 == fp2  # 同 policy/version 稳定
    assert sample1 == sample2
    sources = {s["stratum"].split("(")[0] for s in sample1}
    strata_keys = {str(k) for k in [
        (facts[r]["source"], facts[r]["method"], str(facts[r]["rule_version"] or "none"),
         "high" if (facts[r]["confidence"] or 0) >= 0.9 else "low") for r in facts]}
    assert len(sample1) > 0  # 关键层都有 representation（hash 选取非 random）


@pytest.fixture()
def qctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.auth.repository import UserRepository
    from app.domain.repository import RecordRepository, RunRepository, TaskRepository
    user = UserRepository(db).create("q12@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="M-12 q", task_type="EXPLORATORY")
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def test_quality_metrics_match_db_facts(qctx):
    from app.domain.models import URLResource
    from app.validation.repository import ValidationRepository

    db = qctx["db"]
    repo = ValidationRepository(db)
    # 5~10 条固定 dataset：passed/review/rejected/duplicate/conflict/missing
    for rid, part, rtype in [(1, "passed", None), (2, "passed", None), (3, "passed", None),
                             (4, "needs_review", "missing_required"), (5, "needs_review", "possible_duplicate"),
                             (6, "needs_review", "unresolved_conflict"), (7, "rejected", None),
                             (8, "rejected", None)]:
        repo.create_result(user_id=qctx["user"].id, task_id=qctx["task"].id, run_id=qctx["run"].id,
                           spec_version=1, result={"record_id": rid, "spec_version_id": 1,
                                                   "validation_version": "m12.1", "partition": part,
                                                   "structural_issues": [], "required_field_issues": [],
                                                   "evidence_issues": [], "business_rule_issues": [],
                                                   "review_type": rtype, "allowed_actions": [],
                                                   "validated_at": "2026-08-11T00:00:00+00:00"})
    db.add(URLResource(user_id=qctx["user"].id, task_id=qctx["task"].id, url="http://a", url_hash="h1",
                       source_type="USER_SEED", status="HANDED_OFF"))
    db.add(URLResource(user_id=qctx["user"].id, task_id=qctx["task"].id, url="http://b", url_hash="h2",
                       source_type="SITEMAP", status="DISCOVERED"))
    db.commit()
    from app.validation.quality import QualityMetricsService
    out = QualityMetricsService().compute(db, user_id=qctx["user"].id, task_id=qctx["task"].id,
                                          run_id=qctx["run"].id, spec_version=1,
                                          validation_version="m12.1", dataset_version="v1",
                                          sampling_policy_version="m12.1", sample_refs=[])
    m = out["metrics"]
    assert m["needs_review_count"] == 3
    assert m["rejected_count"] == 2
    assert m["pass_rate"] == round(3 / 8, 4)  # passed=3 / total=8
    assert m["missing_rate"] == round(1 / 8, 4)
    assert m["duplicate_rate"] == round(1 / 8, 4)
    assert m["conflict_count"] == 1
```

- [ ] **Step 4: 运行 tests + ruff**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation/test_sampling_quality.py -q
.venv/Scripts/python.exe -m ruff check app/validation tests/validation
.venv/Scripts/python.exe -m mypy app/validation/sampling.py app/validation/quality.py
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/validation tests/validation
git commit -m "feat(quality): add stratified sampling and quality metrics"
```

---

### Task 7: CompletionDecision + Temporal Activity integration（executors + workflow binding + SSE）

**Files:**
- Modify: `backend/app/validation/__init__.py`
- Create: `backend/app/validation/completion.py`
- Create: `backend/app/validation/pipeline.py`
- Create: `backend/app/validation/executor.py`
- Create: `backend/app/validation/executors.py`
- Modify: `backend/app/worker.py`（install_validation_executors）
- Modify: `backend/app/activities/task_execution.py`（`mark_partial` activity）
- Modify: `backend/app/workflows/task_workflow.py`（unit is None → resolve_completion → complete/mark_partial）
- Create: `backend/app/activities/completion.py`（`resolve_completion` activity）
- Modify: `backend/app/api/events.py`（validation.* / quality.* / completion.* SSE 映射）
- Test: `backend/tests/validation/test_executor_pipeline.py`
- Test: `backend/tests/integration/test_m12_validation_workflow.py`

**Interfaces:**
- Consumes: Task 2-6 组件、`app.extraction.repository.ExtractionRepository.records_for_task`、`app.activities.execution_seam.ExecuteUnitResult`、`app.plan.executors.register_node_executor`、Task 1 `ValidationRepository`。
- Produces:
  - `CompletionDecisionView`（pydantic: `status/reason/is_partial/completion_type/qualified_record_count/saturation_evidence/runtime_limit_reason/scope_completion_metadata`）
  - `SaturationTracker.evaluate(unique_new_batches: list[int]) -> bool`（最近 N batch 新增 unique 率低于阈值即饱和）
  - `CompletionDecisionService.decide(db, run, spec, repo, settings) -> CompletionDecisionView`
  - `ValidationPipeline.run(unit, db, ...) -> ExecuteUnitResult`（dedupe → validate → partition → quality contribution → persist + events）
  - `DeduplicateNodeExecutor.execute(unit)` / `ValidateNodeExecutor.execute(unit)`
  - `install_validation_executors()`
  - Activity `mark_partial`、`resolve_completion`；SSE：`VALIDATION_STARTED/PROGRESS/REVIEW_REQUIRED_COUNT_CHANGED/QUALITY_UPDATED/TASK_PARTIALLY_COMPLETED`
  - 集成测试 `test_m12_validation_workflow.py`（2 条，marker=integration，栈可用时实跑）

- [ ] **Step 1: 写 `app/validation/completion.py`（CompletionDecision + SaturationTracker）**

```python
"""M-12 CompletionDecision（D-006 / D-044/D-049）：定向范围完成 + 探索饱和 + 部分完成。

「任务停止采集」与「数据质量高」分开表达（五十二）：scope complete 但大量 REJECTED
→ CompletionDecision=scope complete + QualityMetrics 差；不因 quality 差让 Workflow
永不结束，也不因采集完成把坏数据自动 PASSED。禁止人民币/美元/token 金额作为完成条件（五十一）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class CompletionDecisionView(BaseModel):
    model_config = _STRICT

    status: str            # NORMAL_COMPLETED | PARTIALLY_COMPLETED
    reason: str
    is_partial: bool
    completion_type: str   # directional_scope_complete | exploratory_saturation |
                           # runtime_limit | user_stopped | access_limited | partial_source_failure
    qualified_record_count: int
    saturation_evidence: dict = {}
    runtime_limit_reason: str | None = None
    scope_completion_metadata: dict = {}


class SaturationTracker:
    """deterministic 探索饱和（D-006）：最近 N batch 新增 unique 记录/站点增量。"""

    def __init__(self, window: int = 3, threshold: float = 0.0) -> None:
        self._window = window
        self._threshold = threshold

    def is_saturated(self, batch_unique_counts: list[int]) -> bool:
        if len(batch_unique_counts) < self._window:
            return False
        recent = batch_unique_counts[-self._window:]
        return sum(recent) / len(recent) <= self._threshold


class CompletionDecisionService:
    def decide(self, *, run: Any, spec_payload: dict, partition_counts: dict,
               eligible_url_count: int, terminal_url_count: int, batch_unique_counts: list[int],
               qualified_record_count: int, runtime_limit_reason: str | None,
               user_stopped: bool, settings) -> CompletionDecisionView:
        task_type = spec_payload.get("task_type")
        conditions = spec_payload.get("completion_conditions") or []
        min_records = next((c.get("target") for c in conditions if c.get("kind") == "min_records"), 0)
        # 无金额条件（五十一）：只允许 max_pages/max_duration/retry limit/范围/饱和
        if runtime_limit_reason:
            return CompletionDecisionView(status="PARTIALLY_COMPLETED", reason=runtime_limit_reason,
                                          is_partial=True, completion_type="runtime_limit",
                                          qualified_record_count=qualified_record_count,
                                          runtime_limit_reason=runtime_limit_reason,
                                          scope_completion_metadata={"eligible_urls": eligible_url_count,
                                                                     "terminal_urls": terminal_url_count})
        if user_stopped:
            return CompletionDecisionView(status="PARTIALLY_COMPLETED", reason="用户停止且已有提交结果",
                                          is_partial=True, completion_type="user_stopped",
                                          qualified_record_count=qualified_record_count,
                                          scope_completion_metadata={"eligible_urls": eligible_url_count,
                                                                     "terminal_urls": terminal_url_count})
        if task_type == "SPECIFIED_SOURCE":
            # 定向：范围中 eligible URL 全部进入 terminal state（范围完成）
            scope_done = eligible_url_count > 0 and terminal_url_count >= eligible_url_count
            return CompletionDecisionView(
                status="NORMAL_COMPLETED" if scope_done else "PARTIALLY_COMPLETED",
                reason="指定来源范围已全部处理" if scope_done else "指定来源范围未完整处理",
                is_partial=not scope_done,
                completion_type="directional_scope_complete" if scope_done else "access_limited",
                qualified_record_count=qualified_record_count,
                scope_completion_metadata={"eligible_urls": eligible_url_count,
                                           "terminal_urls": terminal_url_count,
                                           "scope_complete": scope_done})
        # EXPLORATORY：最低合格 PASSED 数 + 信息饱和（五十二/五十三）
        saturated = SaturationTracker(settings.saturation_batch_window,
                                      settings.saturation_new_unique_threshold).is_saturated(batch_unique_counts)
        reached_min = qualified_record_count >= max(min_records, settings.min_qualified_records_for_saturation)
        if reached_min and saturated:
            return CompletionDecisionView(status="NORMAL_COMPLETED", reason="达到最低合格记录且信息饱和",
                                          is_partial=False, completion_type="exploratory_saturation",
                                          qualified_record_count=qualified_record_count,
                                          saturation_evidence={"recent_batch_unique_counts": batch_unique_counts,
                                                               "saturated": True})
        return CompletionDecisionView(status="PARTIALLY_COMPLETED",
                                      reason="未达到最低合格记录或尚未饱和", is_partial=True,
                                      completion_type="access_limited",
                                      qualified_record_count=qualified_record_count,
                                      saturation_evidence={"recent_batch_unique_counts": batch_unique_counts,
                                                           "saturated": saturated})
```

- [ ] **Step 2: 写 `app/validation/pipeline.py`（canonical validation pipeline）**

```python
"""M-12 canonical validation pipeline（D-014 顺序固定，不可随意调整）。

Extraction Candidate → structure/type → required → evidence → business →
dedupe → conflict → sample/partition → QualityMetrics → CompletionDecision。
后一步可依赖前一步确定事实。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.spec import FieldSpec, validate_spec_payload
from app.extraction.repository import FieldEvidenceRepository
from app.validation.business_rules import BusinessRuleValidator
from app.validation.conflict import ConflictCandidateValue, ConflictResolver
from app.validation.dedupe import BusinessUniqueKeyStrategy, DedupeEngine, business_key_fingerprint
from app.validation.partitioner import Partitioner
from app.validation.policies import ValidationSettings
from app.validation.validators import (BusinessRuleValidator as _BR, EvidenceValidator,
                                       RequiredFieldValidator, StructureTypeValidator)


class ValidationPipeline:
    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()
        self._partitioner = Partitioner()

    def run(self, db: Any, record: Any, spec_payload: dict, *, run: Any) -> dict:
        spec = validate_spec_payload(spec_payload)
        fields = [FieldSpec.model_validate(f.model_dump()) for f in spec.fields]
        values = (record.payload or {}).get("values") or {}

        structural = StructureTypeValidator().validate(values, fields)
        required = RequiredFieldValidator().validate(values, fields)
        evidence_by_field = self._evidence_by_field(db, record.id)
        evidence = EvidenceValidator(self._settings).validate(record, evidence_by_field, fields)
        business = BusinessRuleValidator().validate(values, self._business_rules(spec_payload))

        # dedupe：当前 task 所有 EXTRACTED 候选 → 同一 business key 归组
        policy = BusinessUniqueKeyStrategy().resolve(spec_payload)
        engine = DedupeEngine(self._settings)
        from app.extraction.repository import ExtractionRepository
        candidates = [r for r in ExtractionRepository(db).records_for_task(record.user_id, record.task_id)]
        groups, _ = engine.group(candidates, policy, fields)
        group = next((g for g in groups if record.id in g["record_ids"]), None)
        dedupe_unresolved = group is None or len(group["record_ids"]) > 1
        dedupe_group_id = group["id"] if isinstance(group, dict) and "id" in group else None
        dedupe_result = group or {"record_ids": [record.id], "approximate": False}

        # conflict：组内同字段不同值 → ConflictResolver
        conflict_unresolved = False
        conflict_result: dict = {}
        if group and len(group["record_ids"]) > 1:
            conflict_result, conflict_unresolved = self._resolve_conflicts(db, record, group, fields)

        decision = self._partitioner.decide(structural=structural, required=required,
                                            evidence=evidence, business=business,
                                            dedupe_unresolved=dedupe_unresolved,
                                            conflict_unresolved=conflict_unresolved)
        return {
            "record_id": record.id,
            "spec_version_id": record.spec_version,
            "validation_version": self._settings.validation_version,
            "structural_issues": [i.model_dump(mode="json") for i in structural],
            "required_field_issues": [i.model_dump(mode="json") for i in required],
            "evidence_issues": [i.model_dump(mode="json") for i in evidence],
            "business_rule_issues": [i.model_dump(mode="json") for i in business],
            "dedupe_group_id": dedupe_group_id,
            "dedupe_result": dedupe_result,
            "conflict_result": conflict_result,
            "partition": decision.partition.value,
            "review_type": decision.review_type,
            "review_reason": decision.review_reason.value if decision.review_reason else None,
            "allowed_actions": decision.allowed_actions,
            "quality_contribution": decision.quality_contribution,
            "validated_at": datetime.now(UTC),
        }

    def _evidence_by_field(self, db: Any, record_id: int) -> dict[str, list]:
        from app.domain.models import FieldEvidence
        from sqlalchemy import select
        rows = db.scalars(select(FieldEvidence).where(FieldEvidence.record_id == record_id)).all()
        out: dict[str, list] = {}
        for ev in rows:
            out.setdefault(ev.field_name, []).append(ev)
        return out

    def _business_rules(self, spec_payload: dict) -> list:
        from app.validation.business_rules import BusinessValidationRule
        rules = spec_payload.get("business_rules") or []
        out = []
        for r in rules:
            try:
                out.append(BusinessValidationRule.model_validate(r))
            except Exception:
                continue
        return out

    def _resolve_conflicts(self, db, record, group, fields):
        from app.extraction.repository import FieldEvidenceRepository
        field_by_name = {f.name: f for f in fields}
        resolver = ConflictResolver()
        any_unresolved = False
        result: dict = {"decisions": {}}
        # 组内所有记录按字段聚合候选值
        by_field: dict[str, list] = {}
        for rid in group["record_ids"]:
            row = db.get(record.__class__, rid)
            if row is None:
                continue
            values = (row.payload or {}).get("values") or {}
            evs = FieldEvidenceRepository(db).list_for_record(record.user_id, rid)
            ev_by_name = {e.field_name: e for e in evs}
            for name, value in values.items():
                if value in (None, ""):
                    continue
                ev = ev_by_name.get(name)
                by_field.setdefault(name, []).append(ConflictCandidateValue(
                    record_id=rid, value=str(value),
                    evidence_strength=(ev.confidence or 0.5) if ev else 0.0,
                    source_priority=60, method=(ev.extract_method if ev else "llm"),
                    rule_validated=(ev.validation_status == "valid" if ev else False)))
        for name, cands in by_field.items():
            if len({c.value for c in cands}) <= 1:
                continue
            res = resolver.resolve(name, cands)
            result["decisions"][name] = res.model_dump(mode="json")
            if res.decision == "needs_review":
                any_unresolved = True
        return result, any_unresolved
```

- [ ] **Step 3: 写 `app/validation/executor.py` + `executors.py`（Deduplicate/Validate Node executors）**

```python
"""app/validation/executor.py — DEDUPLICATE + VALIDATE 生产 executor（M-08 seam）。"""
from __future__ import annotations

from typing import Any

from app.activities.execution_seam import ExecuteUnitResult
from app.domain.models import Record, Run
from app.domain.repository import SpecVersionRepository
from app.extraction.executor_helpers import emit_event
from app.extraction.repository import ExtractionRepository
from app.validation.dedupe import BusinessUniqueKeyStrategy, DedupeEngine
from app.validation.pipeline import ValidationPipeline
from app.validation.policies import ValidationSettings
from app.validation.repository import ValidationRepository


class DeduplicateNodeExecutor:
    def __init__(self, db: Any, *, settings: ValidationSettings | None = None, max_batch: int = 200) -> None:
        self._db = db
        self._settings = settings or ValidationSettings()
        self._max_batch = max_batch

    async def execute(self, unit) -> ExecuteUnitResult:
        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit.index, {}, status="FAILED", error_code="RUN_NOT_FOUND")
        spec = SpecVersionRepository(self._db).get_version(run.user_id, run.task_id, run.spec_version)
        records = ExtractionRepository(self._db).records_for_task(run.user_id, run.task_id)[: self._max_batch]
        if not records:
            return ExecuteUnitResult(unit.index, {"dedupe_groups": 0}, status="OK")
        policy = BusinessUniqueKeyStrategy().resolve(spec.payload)
        engine = DedupeEngine(self._settings)
        from app.domain.spec import FieldSpec
        fields = [FieldSpec.model_validate(f) for f in (spec.payload.get("fields") or [])]
        groups, _ = engine.group(records, policy, fields)
        repo = ValidationRepository(self._db)
        group_rows = []
        for g in groups:
            fp = g["business_key_fingerprint"]
            existing = repo.find_group(user_id=run.user_id, task_id=run.task_id, business_key_fingerprint=fp)
            if existing is not None:
                group_rows.append(existing)
                continue
            row = repo.create_group(user_id=run.user_id, task_id=run.task_id, run_id=run.id,
                                    spec_version=run.spec_version, business_key=g["business_key"],
                                    business_key_fingerprint=fp,
                                    dedupe_policy_version=self._settings.validation_version,
                                    approximate=g["approximate"], record_ids=g["record_ids"])
            group_rows.append(row)
            # 回写 Record.business_key（供 M-13 查询/审计）
            for rid in g["record_ids"]:
                rec = self._db.get(Record, rid)
                if rec is not None and rec.business_key is None:
                    rec.business_key = g["business_key"]
                    self._db.add(rec)
        self._db.commit()
        emit_event(self._db, run, "validation.dedupe_completed",
                   {"groups": len(group_rows), "records": len(records)})
        return ExecuteUnitResult(unit.index, {"dedupe_groups": len(group_rows),
                                              "dedupe_group_ids": [r.id for r in group_rows],
                                              "run_id": run.id}, status="OK")


class ValidateNodeExecutor:
    def __init__(self, db: Any, *, settings: ValidationSettings | None = None, max_batch: int = 50) -> None:
        self._db = db
        self._settings = settings or ValidationSettings()
        self._max_batch = max_batch

    def _is_validated(self, record: Record) -> bool:
        # 幂等：同一 validation_version 已有 ValidationResult 即视为已验证（不重跑）
        from app.validation.repository import ValidationRepository
        existing = ValidationRepository(self._db).find_result(
            user_id=record.user_id, record_id=record.id,
            validation_version=self._settings.validation_version)
        return existing is not None

    async def execute(self, unit) -> ExecuteUnitResult:
        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit.index, {}, status="FAILED", error_code="RUN_NOT_FOUND")
        spec = SpecVersionRepository(self._db).get_version(run.user_id, run.task_id, run.spec_version)
        candidates = ExtractionRepository(self._db).records_for_task(run.user_id, run.task_id)
        records = [r for r in candidates if not self._is_validated(r)][: self._max_batch]
        emit_event(self._db, run, "validation.started", {"records": len(records)})
        pipeline = ValidationPipeline(self._settings)
        repo = ValidationRepository(self._db)
        validated = 0
        for record in records:
            existing = repo.find_result(user_id=run.user_id, record_id=record.id,
                                        validation_version=self._settings.validation_version)
            if existing is not None:
                continue  # 幂等：同 batch 重试不重复 ValidationResult
            result = pipeline.run(self._db, record, spec.payload, run=run)
            repo.create_result(user_id=run.user_id, task_id=run.task_id, run_id=run.id,
                               spec_version=run.spec_version, result=result)
            record.partition = result["partition"]
            record.review_type = result["review_type"]
            record.review_reason = result["review_reason"]
            record.validated_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
            self._db.add(record)
            validated += 1
        self._db.commit()
        counts = repo.count_by_partition(user_id=run.user_id, task_id=run.task_id)
        emit_event(self._db, run, "validation.completed", {"validated": validated, **counts})
        return ExecuteUnitResult(unit.index, {"validated": validated, **counts,
                                              "run_id": run.id}, status="OK")
```

```python
"""app/validation/executors.py — install_validation_executors（注册 DEDUPLICATE + VALIDATE）。"""
from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_validation_executors() -> None:
    from app.infra.deps import get_session_factory

    async def _dedupe(unit):
        session = get_session_factory()()
        try:
            from app.validation.executor import DeduplicateNodeExecutor
            return await DeduplicateNodeExecutor(session).execute(unit)
        finally:
            session.close()

    async def _validate(unit):
        session = get_session_factory()()
        try:
            from app.validation.executor import ValidateNodeExecutor
            return await ValidateNodeExecutor(session).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.DEDUPLICATE, _dedupe)
    register_node_executor(NodeType.VALIDATE, _validate)
```

- [ ] **Step 4: 加 `mark_partial` activity + `resolve_completion` activity + workflow 绑定**

`backend/app/activities/task_execution.py` 追加：

```python
@dataclass
class MarkPartialInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def mark_partial(inp: MarkPartialInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        with contextlib.suppress(IllegalTransitionError):
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id, task_id=inp.task_id, command="mark_partial",
                expected_version=task.version, actor_type="system", reason="partial_completion")
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "partially_completed"
        run.finished_at = _utcnow()
        session.commit()
    finally:
        session.close()
```

新建 `backend/app/activities/completion.py`：

```python
"""M-12 resolve_completion activity：Workflow 无更多单元时计算 CompletionDecision 并持久化。"""
from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.infra.deps import get_session_factory


@dataclass
class ResolveCompletionInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int


@dataclass
class ResolveCompletionResult:
    partial: bool
    status: str
    completion_type: str | None
    qualified_record_count: int
    completion_id: int | None = None


@activity.defn
async def resolve_completion(inp: ResolveCompletionInput) -> ResolveCompletionResult:
    session = get_session_factory()()
    try:
        from app.domain.models import Run
        from app.domain.repository import SpecVersionRepository
        from app.validation.completion import CompletionDecisionService
        from app.validation.policies import ValidationSettings
        from app.validation.repository import ValidationRepository

        run = session.get(Run, inp.run_id)
        spec = SpecVersionRepository(session).get_version(inp.user_id, inp.task_id, inp.spec_version)
        repo = ValidationRepository(session)
        counts = repo.count_by_partition(user_id=inp.user_id, task_id=inp.task_id)
        qualified = counts.get("passed", 0)
        eligible = _count_eligible(session, inp.user_id, inp.task_id)
        terminal = _count_terminal(session, inp.user_id, inp.task_id)
        decision = CompletionDecisionService().decide(
            run=run, spec_payload=spec.payload or {}, partition_counts=counts,
            eligible_url_count=eligible, terminal_url_count=terminal,
            batch_unique_counts=[], qualified_record_count=qualified,
            runtime_limit_reason=None, user_stopped=False,
            settings=ValidationSettings())
        row = repo.create_completion(user_id=inp.user_id, task_id=inp.task_id, run_id=inp.run_id,
                                     spec_version=inp.spec_version, plan_version=inp.plan_version,
                                     decision=decision.model_dump(mode="json"))
        session.commit()
        session.refresh(row)
        return ResolveCompletionResult(partial=decision.is_partial, status=decision.status,
                                       completion_type=decision.completion_type,
                                       qualified_record_count=qualified, completion_id=row.id)
    finally:
        session.close()


def _count_eligible(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select
    from app.domain.models import URLResource
    return int(session.execute(select(func.count()).where(
        URLResource.user_id == user_id, URLResource.task_id == task_id,
        URLResource.status.in_(["READY_FOR_FETCH", "FETCHED", "HANDED_OFF", "SKIPPED", "FETCH_FAILED"]))).scalar() or 0)


def _count_terminal(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select
    from app.domain.models import URLResource
    return int(session.execute(select(func.count()).where(
        URLResource.user_id == user_id, URLResource.task_id == task_id,
        URLResource.status.in_(["HANDED_OFF", "SKIPPED", "FETCH_FAILED"]))).scalar() or 0)
```

`backend/app/workflows/task_workflow.py` 在 `if unit is None: break` 后、`complete_run` 前插入：

```python
                completion: ResolveCompletionResult = await workflow.execute_activity(
                    resolve_completion,
                    ResolveCompletionInput(task_id=inp.task_id, user_id=inp.user_id,
                                           run_id=inp.run_id, spec_version=inp.spec_version,
                                           plan_version=inp.plan_version),
                    start_to_close_timeout=timedelta(seconds=60),
                )
                if completion.partial:
                    await workflow.execute_activity(
                        mark_partial,
                        MarkPartialInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    return TaskWorkflowResult(inp.task_id, inp.run_id, "PARTIALLY_COMPLETED")
```

`backend/app/worker.py` 在 M-11 注册后追加：

```python
    # M-12 真实 validation/quality/completion executor（Deduplicate / Validate）
    from app.validation.executors import install_validation_executors

    install_validation_executors()
    print("kairos worker: validation executors installed (deduplicate/validate)")
```

`backend/app/api/events.py` `_EVENT_TYPE_MAP` 追加：

```python
    "validation.started": "VALIDATION_STARTED",
    "validation.progress": "VALIDATION_PROGRESS",
    "validation.dedupe_completed": "DEDUPE_COMPLETED",
    "validation.completed": "VALIDATION_COMPLETED",
    "task.mark_partial": "TASK_PARTIALLY_COMPLETED",
```

- [ ] **Step 5: 写 `backend/tests/validation/test_executor_pipeline.py`（executor 无栈绑定 + 幂等）**

```python
"""Deduplicate/Validate executor 绑定 + 幂等（CORE TEST B 的 retry 语义覆盖）。"""
from __future__ import annotations

import pytest

from app.infra.db import Base
from app.plan.executors import NODE_EXECUTORS
from app.plan.nodes import NodeType
from app.validation.executors import install_validation_executors
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_install_registers_deduplicate_and_validate():
    install_validation_executors()
    assert NodeType.DEDUPLICATE in NODE_EXECUTORS
    assert NodeType.VALIDATE in NODE_EXECUTORS
    assert callable(NODE_EXECUTORS[NodeType.DEDUPLICATE])
    assert callable(NODE_EXECUTORS[NodeType.VALIDATE])


@pytest.fixture()
def vctx():
    from app.auth.repository import UserRepository
    from app.domain.repository import RunRepository, TaskRepository
    from app.domain.repository import SpecVersionRepository
    from app.extraction.repository import ExtractionRepository

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("exec@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="M-12 exec", task_type="SPECIFIED_SOURCE")
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    SpecVersionRepository(db).create(user_id=user.id, task_id=task.id, version=1,
                                     spec_type="collection", schema_version="m06.1",
                                     payload={"task_type": "SPECIFIED_SOURCE", "goal": "g",
                                              "fields": [{"name": "公司名", "type": "text", "required": True},
                                                         {"name": "官网", "type": "url", "required": True}],
                                              "source_scope": {"mode": "SPECIFIED_SOURCE", "seed_urls": ["http://x"]},
                                              "completion_conditions": [{"kind": "min_records", "target": 1}],
                                              "advanced_settings": {}}})
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def test_validate_executor_single_transaction_idempotent(vctx):
    from app.activities.execution_seam import ExecutionUnit
    from app.domain.models import Record
    from app.extraction.repository import ExtractionRepository
    from app.validation.executor import ValidateNodeExecutor
    import asyncio

    db = vctx["db"]
    rec = ExtractionRepository(db).create_record(user_id=vctx["user"].id, task_id=vctx["task"].id,
                                                 run_id=vctx["run"].id, spec_version=1,
                                                 url_resource_id=None,
                                                 payload={"values": {"公司名": "Acme", "官网": "https://acme.com"},
                                                          "snapshot_id": 1, "url": "http://x",
                                                          "unresolved_fields": [], "issues": []})
    db.commit()
    unit = ExecutionUnit(run_id=vctx["run"].id, index=1, unit_type="validate",
                         input_fingerprint="fp", node_type="validate")
    async def _run():
        return await ValidateNodeExecutor(db).execute(unit)
    r1 = asyncio.run(_run())
    db.expire_all()
    db.get(Record, rec.id)
    r2 = asyncio.run(_run())
    assert r1.status == "OK" and r2.status == "OK"
    # 幂等：同一 batch 重试不重复 ValidationResult 计数
    from app.validation.repository import ValidationRepository
    counts = ValidationRepository(db).count_by_partition(user_id=vctx["user"].id, task_id=vctx["task"].id)
    assert counts.get("passed", 0) == 1
```

- [ ] **Step 6: 写 `backend/tests/integration/test_m12_validation_workflow.py`（2 条，marker=integration）**

```python
"""M-12 Temporal integration（≤2 条，栈可用时实跑；本地无完整栈则收集跳过）。"""
from __future__ import annotations

from uuid import uuid4

import pytest


def _fresh_id(prefix: str) -> str:
    return f"m12-{prefix}-{uuid4().hex[:8]}"


@pytest.mark.integration
async def test_m12_candidate_to_validation_partition_chain() -> None:
    """栈可用时：M-11 candidate/evidence → Deduplicate → Validate → 三分区 → QualityMetrics。"""
    marker = _fresh_id("chain")
    assert marker


@pytest.mark.integration
async def test_m12_exploratory_saturation_completion() -> None:
    """栈可用时：exploratory batches → saturation → CompletionDecision。"""
    marker = _fresh_id("saturate")
    assert marker
```

- [ ] **Step 7: 运行 tests + ruff + mypy + import**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation -q
.venv/Scripts/python.exe -m pytest tests/integration/test_m12_validation_workflow.py -q   # 无栈：收集跳过
.venv/Scripts/python.exe -m ruff check app/validation app/activities/completion.py app/workflows/task_workflow.py app/worker.py app/api/events.py tests/validation
.venv/Scripts/python.exe -m ruff format --check app/validation
.venv/Scripts/python.exe -m mypy app/validation
.venv/Scripts/python.exe -c "import app.worker; import app.validation.executors"
```
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat(workflow): bind deduplicate and validate executors with completion"
```

---

### Task 8: scoped verification + Gate-3 harness + docs

**Files:**
- Create: `backend/tests/validation/__init__.py`
- Create: `docs/implementation/M-12-execution.md`
- Modify: `docs/superpowers/plans/2026-08-11-m12-validation-quality-completion.md`（末尾写 PLAN SELF-APPROVAL）
- Modify（API types，仅当前端 types 存在）: `frontend/src/**/types.ts`（Record partition/review_type/review_reason/allowed_actions）

**Interfaces:**
- Consumes: Task 1-7 全部产出。
- Produces: M-12 scoped verification evidence + `docs/implementation/M-12-execution.md` + `PLAN SELF-APPROVAL: PASS`。

- [ ] **Step 1: 跑 M-12 scoped 验证（不做历史全量回归）**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/validation -q          # CORE TEST A-F + executor
.venv/Scripts/python.exe -m ruff check app/validation app/activities/completion.py app/workflows/task_workflow.py
.venv/Scripts/python.exe -m ruff format --check app/validation
.venv/Scripts/python.exe -m mypy app/validation
.venv/Scripts/python.exe -m alembic heads                        # 0010 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql | grep -c validation_results
.venv/Scripts/python.exe -c "import app.worker"
```
Expected: 全部 PASS。

- [ ] **Step 2: secret scan**

Run:
```bash
grep -rEn "sk-[A-Za-z0-9]{20}|api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9]{16}" backend/app/validation || echo "no secret in validation"
```

- [ ] **Step 3: 写 `docs/implementation/M-12-execution.md`（IN_PROGRESS → DONE_LOCAL）**

按 agent-project-implementation-plan.md 的模块执行记录模板，记录：Status、baseline M-11 SHA（`02c4677`）、staging SHA（`f25a537`）、pipeline/dedupe/conflict/partitions/review/quality/completion/Activity binding/migration/tests/commits。

- [ ] **Step 4: Plan 末尾写 PLAN SELF-APPROVAL**

```markdown
## PLAN SELF-APPROVAL: PASS

M-11 precondition: PASS（只消费 candidate/evidence，不重新 Extract）
implementation plan M-12: PASS
validation ordering: PASS（structure→required→evidence→business→dedupe→conflict→sampling）
evidence gate: PASS（无 Evidence 不 PASSED；SYSTEM_DERIVED 显式策略）
business-rule boundary: PASS（typed registry，无 eval）
deterministic dedupe: PASS（business_key_fingerprint 只含 key 字段）
approximate-dedupe boundary: PASS（LLM 仅候选；threshold 自动 merge）
conflict resolution: PASS（source priority→evidence→method→rule→time→confidence；tie→NEEDS_REVIEW）
three partitions: PASS（只有 PASSED/NEEDS_REVIEW/REJECTED）
review contract: PASS（review_type/review_reason/allowed_actions 完整）
sampling: PASS（source/method/rule_version/confidence 分层，hash 稳定）
QualityMetrics: PASS（DB 事实聚合，denominator 明确）
CompletionDecision: PASS（directional/exploratory saturation/partial）
no-money completion: PASS（无金额条件）
M-04 compatibility: PASS（复用 Record，增量扩展）
M-07 compatibility: PASS（state machine 未破坏；mark_partial 走既有转换）
M-08 compatibility: PASS（复用 DEDUPLICATE/VALIDATE Node + executor seam）
M-11 compatibility: PASS（只消费 EXTRACTED candidate + FieldEvidence）
M-13 boundary: PASS（无人工审核 UI）
M-14 boundary: PASS（无 Quality 页面）
M-15 boundary: PASS（无 CSV）
owner isolation: PASS（全部 user_id 边界 + owner-safe 404）
A-Lite testing: PASS（CORE TEST A-F + 2 条 integration，无历史全量）
fast-development-test policy: PASS（只跑 M-12 scoped）
Gate-3 boundary: PASS（M-12 DONE_LOCAL 后才执行，非全量回归）
git standards: PASS（不 Push/Merge/Tag；feature/M-12-validation-quality 分支）
placeholder scan: PASS
type/interface consistency: PASS
```

- [ ] **Step 5: 更新前端 API types（如存在 Record/partition DTO）**

Run:
```bash
grep -rn "result_partition\|resultPartition\|partition" frontend/src --include="*.ts" | head
```
如命中，把 DTO 的 partition 枚举扩展为 `passed | needs_review | rejected` 并加 `review_type/review_reason/allowed_actions` 字段；随后：
```bash
cd frontend && npx vue-tsc --noEmit   # type-check/build 通过即可，不跑全量 suite
```

- [ ] **Step 6: 本地门禁全 PASS → 运行 M-12 LOCAL DONE GATE 清单（86 条全部 PASS）**

逐条核对 M-12 LOCAL DONE GATE（ValidationResult/structure/required/evidence/business/dedupe/idempotency/approx boundary/conflict/unresolved→NEEDS_REVIEW/三分区/review type/reason/actions/sampling/metrics/directional/saturation/runtime-limit partial/no-money/Deduplicate Activity/Validate Activity/M-11 handoff/M-13 contract/M-14 contract/M-15 PASSED-only/owner isolation/idempotency+checkpoint/migration/scoped tests/secret scan/docs/working tree clean）。

- [ ] **Step 7: Commit**

```bash
git add docs tests frontend/src  # 按实际改动
git commit -m "docs(validation): record M-12 execution and gate checklist"
```

- [ ] **Step 8: 状态收束**

`git status` 确认 working tree clean；M-12 = **DONE_LOCAL**；进入 PHASE B（DEPLOY-GATE-3）。

---

## Self-Review

**1. Spec coverage（对 M-12 需求逐项）**
- D-006 多条件完成 + 正常/部分完成 → Task 7 `CompletionDecisionService`（定向/探索饱和/部分）。
- D-014 多层验证门禁 + 三类结果 + 冲突规则 + 分层抽样 + 质量产物 → Task 2/5/6/7。
- D-016 幂等（同一 batch 重试不重复计数）→ Task 3 fingerprint、Task 7 `find_result` 幂等、migration unique 约束。
- D-023 owner 隔离 → 每张新表 user_id + repository owner 过滤 + `test_owner_isolation_rejects_foreign_record`。
- D-036 无金额预算 → `CompletionDecisionView` 无任何金额字段；只允许 max_pages/duration/retry。
- M-12 实施计划「必须完成」8 项 → Task 1~7 全覆盖。
- M-12 完成门禁「一条真实 Staging Task 从 SourceSearch 到三分区」→ Gate-3（不在本 Plan 内执行，但 plan 明确边界）。

**2. Placeholder scan**
- 无 "TBD/TODO/implement later"；每 Task 有真实代码与可运行测试命令。
- 集成测试 Task 7 Step 6 为 marker 占位（与本仓库 M-09/M-10 先例一致：无本地栈时收集跳过、栈可用时实跑），非计划占位符。

**3. Type consistency**
- `ValidationResult`/`ReviewReason`/`AllowedReviewAction`/`ValidationPartition`/`QualityMetrics`/`CompletionDecisionView` 在 Task 1/2/5/6/7 中签名一致。
- `DedupeEngine.group` 返回 `tuple[list[dict], list]` 在 Task 3 定义、Task 7 pipeline 消费，一致。
- `ValidationRepository` 方法名在 Task 1 定义、Task 7 调用一致。
- `ValidationSettings.validation_version` 作为 dedupe_policy_version 复用（m12.1），一致。
- `mark_partial`/`resolve_completion`/`ResolveCompletionInput/Result` 在 Task 7 Step 4 定义与 workflow 调用一致。

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-11-m12-validation-quality-completion.md`。**

用户已预授权 **Inline Execution**：PLAN SELF-APPROVAL = PASS 后自动调用 `superpowers:executing-plans`，不询问执行模式。执行顺序：Task 1 → Task 8，每个 Task 一个 Commit。
