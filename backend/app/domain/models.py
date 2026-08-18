"""Core execution domain models (M-04).

Every business table carries an explicit NOT NULL user_id; owners are never
derived implicitly. States are stored as canonical uppercase strings (see
app.state.states). Optimistic concurrency uses ``version`` on mutable rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULL until Goal Understanding resolves it to a canonical TaskType (M-06).
    task_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_spec_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Template reference kept when the Task was created from a CollectionTemplate (D-047).
    # Runtime facts always come from the generated CollectionSpecVersion, never from the template.
    template_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M-15: 软删除前状态，restore 时回到该终态（不破坏 Run execution facts）。
    restore_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    """Append-only Chat history for the single Task workspace (D-033).

    Never UPDATE a historical message. Agent messages that reference a structured
    object (CollectionSpecDraft, GoalUnderstandingResult, clarification,
    model_required, error) carry a typed ``ref_type`` / ``ref_id`` instead of
    flattening business facts into plain markdown.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict | None] = mapped_column("meta", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CollectionSpecDraft(Base):
    """Editable current-candidate spec for one Task (D-004).

    Saving a draft does NOT create a CollectionSpecVersion; only confirm_spec
    freezes an immutable version. One draft per task.
    """

    __tablename__ = "collection_spec_drafts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CollectionTemplate(Base):
    """Versioned collection template (D-047 / D-054).

    ``template_id`` is the stable logical identity; each edit appends a new
    ``version`` row and flips ``is_current`` (same pattern as M-03 ModelConfig).
    Old versions are immutable history and keep working for Tasks that referenced
    them. A template stores a CollectionSpec skeleton only — never Run/Record/
    Evidence/Checkpoint execution state.
    """

    __tablename__ = "collection_templates"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_ct_template_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    goal_template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    field_schema: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    completion_conditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    advanced_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    field_expansion: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    default_model_config_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CollectionSpecVersion(Base):
    __tablename__ = "collection_spec_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_csv_task_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_type: Mapped[str] = mapped_column(String(30), nullable=False, default="collection")
    schema_version: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanVersion(Base):
    """Immutable plan version (M-08). v1 is never modified; replan creates vN+1."""

    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_pv_task_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_plan_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    model_config_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registry_versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generation_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")
    trigger_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    replan_evidence_refs: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    diff_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionPreflightResult(Base):
    """Immutable readiness fact for one frozen Plan and executor manifest."""

    __tablename__ = "execution_preflight_results"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "plan_version",
            "capability_manifest_version",
            name="uq_execution_preflight_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_manifest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    search_config_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    search_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NodeRun(Base):
    __tablename__ = "node_runs"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_node_runs_run_node_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Legacy rows predate frozen Plan node identity and remain nullable.
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NodeAttempt(Base):
    __tablename__ = "node_attempts"
    __table_args__ = (UniqueConstraint("node_run_id", "attempt", name="uq_na_node_attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_run_id: Mapped[int] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class URLResource(Base):
    __tablename__ = "url_resources"
    __table_args__ = (UniqueConstraint("task_id", "url_hash", name="uq_ur_task_url_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="seed")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DISCOVERED")
    # M-09 frontier metadata（migration 0007）
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    discovery_source: Mapped[str] = mapped_column(String(40), nullable=False, default="USER_SEED")
    parent_url_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    discovery_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M-10 fetch 审计（migration 0008）
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetch_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SiteFetchStrategy(Base):
    """站点级成功抓取策略（D-009 策略复用 / 六十四 TTL 失效重探测）。

    owner-safe：user_id + site_host 唯一；可被同用户后续任务复用，但**不能成为
    永久 bypass authorization**（二十二）——每个 URL 执行时仍重新校验
    AccessDecision/robots/scope，策略只决定“用什么工具”，不决定“能否访问”。
    """

    __tablename__ = "site_fetch_strategies"
    __table_args__ = (UniqueConstraint("user_id", "site_host", name="uq_sfs_user_site"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_host: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="static")
    tool: Mapped[str] = mapped_column(String(30), nullable=False, default="http")
    tool_version: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    structure_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credential_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="probing")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PageSnapshot(Base):
    """Immutable 网页抓取观察（M-04 foundation + M-10 fetch metadata）。

    每次真实抓取形成一行 observation：同一内容重抓复用 content Blob（content-addressable），
    但仍保留新 observation 行 + snapshot_version 递增 + prior_snapshot_id 链，
    从而保留“何时再次抓取”的审计事实（十三）。工具调用/原始内容只读，绝不被覆盖。
    """

    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    url_resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("url_resources.id"), nullable=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool: Mapped[str] = mapped_column(String(30), nullable=False, default="http")
    tool_version: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redirect_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    escalation_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 相同内容重抓：observation 链（version 递增 + prior 指向上一 observation）
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prior_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credential_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 脱敏，无明文
    http_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # allowlist 摘要
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="stored")
    # M-11 提取失败账本（migration 0017）：NULL=待提取 / "failed"=合法提取失败（跳过重处理）。
    # 只标记一次提取结果，不覆盖观察数据，保持 snapshot 不可变语义。
    extraction_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    url_resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("url_resources.id"), nullable=True
    )
    business_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    partition: Mapped[str] = mapped_column(String(30), nullable=False, default="passed")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ---- M-12 validation/partition（migration 0010，nullable 兼容）----
    review_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # ---- M-13 data review（migration 0011，nullable 兼容）----
    data_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FieldEvidence(Base):
    """Immutable field-level evidence chain (M-04 foundation + M-11 extension).

    D-072: the bounded raw_snippet is kept so the evidence chain survives heavy-file
    lifecycle cleanup; never relies on the raw snapshot existing forever.
    """

    __tablename__ = "field_evidence"
    __table_args__ = (
        UniqueConstraint(
            "record_id", "field_name", "extract_method", name="uq_fe_record_field_method"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshots.id"), nullable=True)
    extract_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # ---- M-11 evidence-chain extension (all set by M-11; nullable for expand compat) ----
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url_resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    model_config_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    issue_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExtractorRuleVersion(Base):
    """Immutable validated site rule version (D-010 / 二十：规则版本不可变、可回滚).

    A rule version is never mutated; structure change creates vN+1. Only
    schema-validated + representative-validated + threshold-passed rules become
    ACTIVE. Rollback sets the target version ACTIVE and the previous one STALE.
    """

    __tablename__ = "extractor_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "site_host", "field_name", "version", name="uq_er_site_field_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_host: Mapped[str] = mapped_column(String(255), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_type: Mapped[str] = mapped_column(String(10), nullable=False, default="css")
    selector: Mapped[str] = mapped_column(String(1000), nullable=False)
    value_transform: Mapped[str] = mapped_column(String(50), nullable=False, default="identity")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft"
    )  # DRAFT|VALIDATED|ACTIVE|STALE|NEEDS_REVALIDATION|REJECTED
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Approval(Base):
    """Scoped, expiring, fingerprint-bound human approval (M-08 / D-017).

    No GLOBAL_FOREVER / ANY_PARAMETERS. Binding: owner + spec_version +
    plan_version + node identity + parameter_fingerprint + approved_scope + expiry.
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    node_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parameter_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_scope: Mapped[str] = mapped_column(String(30), nullable=False, default="this_action")
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="single")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credential_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    # M-16 并发幂等加固：READY 状态按 (user, task, request_fingerprint) 唯一。
    # 部分唯一（PG postgresql_where / SQLite sqlite_where）→ 相同导出并发只允许一行。
    __table_args__ = (
        Index(
            "ix_artifacts_user_task_fp_ready",
            "user_id",
            "task_id",
            "request_fingerprint",
            unique=True,
            postgresql_where=text("status = 'ready'"),
            sqlite_where=text("status = 'ready'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    export_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filter_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ---- M-15 export/artifact lifecycle（migration 0012，expand-only）----
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False, default="user")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("node_runs.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_ik_scope_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    result_ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UnderstandingAttempt(Base):
    """Goal Understanding 幂等 attempt（M-06 request-lifecycle 修复）。

    身份 = (task_id, source_message_id, input_fingerprint)。partial unique index
    (status='running') 是跨 API 进程的并发兜底：同一输入同时两个 /understand，
    只有第一个能真正调 Provider，第二个返回 IN_PROGRESS。
    只存安全审计 metadata（config_id/version/provider/model/duration），绝不存 Secret。
    """

    __tablename__ = "understanding_attempts"
    __table_args__ = (
        Index(
            "ix_understanding_attempts_running",
            "task_id",
            "source_message_id",
            "input_fingerprint",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
        Index(
            "ix_understanding_attempts_identity",
            "task_id",
            "source_message_id",
            "input_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    trigger_source: Mapped[str] = mapped_column(String(30), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 审计 metadata（无 Secret）
    model_config_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 成功时保存结果，供 reload/reconcile 复用（不再次调 Provider）
    result_ref_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    spec_draft_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "batch_identity", name="uq_cp_run_batch"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("node_runs.id"), nullable=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_object_refs: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ValidationResult(Base):
    """M-12 canonical 单条 Record 验证结果（D-014）。

    （record_id, validation_version）唯一：验证规则升级时允许新的 ValidationAttempt，
    不允许静默修改旧 validation history（D-014 / 模块需求 11）。
    """

    __tablename__ = "validation_results"
    __table_args__ = (
        UniqueConstraint("record_id", "validation_version", name="uq_vr_record_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    spec_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
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
    """M-12 确定性去重簇（D-014 / 模块需求 22-23）。

    相同 task + business_key_fingerprint 唯一；Evidence/ExtractionCandidate 全部保留，
    dedupe 合并业务视图、不删除证据历史。
    """

    __tablename__ = "dedupe_clusters"
    __table_args__ = (
        UniqueConstraint("task_id", "business_key_fingerprint", name="uq_dc_task_fp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    business_key: Mapped[str] = mapped_column(String(500), nullable=False)
    business_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    approximate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="grouped")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldConflict(Base):
    """M-12 跨来源字段冲突（D-014 冲突规则）。

    未裁决冲突 state=unresolved；保留全部 candidate_values，绝不静默选值（模块需求 23/26）。
    """

    __tablename__ = "field_conflicts"
    __table_args__ = (
        UniqueConstraint("record_id", "field_name", "state", name="uq_fc_record_field_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_id: Mapped[int] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    candidate_values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="unresolved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualitySnapshot(Base):
    """M-12 不可变质量快照（D-014 / 模块需求 54）。

    绑定 task/run/spec/validation/sampling policy/dataset version；后续数据变化不
    静默改写历史 Quality 报告。
    """

    __tablename__ = "quality_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_version: Mapped[str] = mapped_column(String(30), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    sampling_policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    denominators: Mapped[dict] = mapped_column(JSON, nullable=False)
    sample_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompletionDecision(Base):
    """M-12 完成判定（D-006 / 模块需求 43-52）。

    不含任何人民币/美元/费用/token 金额字段（D-036）。「任务停止采集」与「数据质量
    高」分开表达：status 只描述范围/饱和/限制完成，quality 由 QualitySnapshot 表达。
    """

    __tablename__ = "completion_decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecordFieldOverride(Base):
    """M-13 人工字段修正（D-042）。保留 original/final/value_source/modified_by/modified_at。

    禁止覆盖 PageSnapshot 与 FieldEvidence；Record.payload 最终值 = payload 叠加覆写。
    """

    __tablename__ = "record_field_overrides"
    __table_args__ = (UniqueConstraint("record_id", "field_name", name="uq_rfo_record_field"),)

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


class ResourceLease(Base):
    """M-16 跨进程资源租赁（D-071 三级调度协调事实，非业务 Checkpoint）。

    heartbeat 只表达「资源仍被占用」事实，绝不充当业务 Checkpoint（M-04/M-07
    规则不变）；TTL + reaper 回收异常退出 worker 的 slot。
    """

    __tablename__ = "resource_leases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)  # global|user|resource_class
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    holder_type: Mapped[str] = mapped_column(String(30), nullable=False)  # run|node
    holder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    resource_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active | released | expired
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_leases_scope_key", "scope", "scope_key"),
        Index("ix_leases_state_expires", "state", "expires_at"),
    )


class DomainCircuitBreaker(Base):
    """M-16 部署级域名熔断器（保护目标域名，无 owner；用户 UI 只见脱敏文案）。"""

    __tablename__ = "domain_circuit_breakers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="CLOSED")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    open_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    half_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # HALF_OPEN 期间只放行一次请求（单探针）；用条件 UPDATE 原子认领，避免多 worker 并发放行。
    half_open_probe_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
