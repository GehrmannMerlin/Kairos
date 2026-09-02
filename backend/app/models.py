from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(Text)
    permission_mode: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.workspace_id"), nullable=True)
    spec_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("collection_spec_versions.spec_version_id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskRun(Base):
    __tablename__ = "task_runs"

    task_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    workflow_id: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    task_run_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionSpecVersion(Base):
    __tablename__ = "collection_spec_versions"

    spec_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(32))
    goal: Mapped[str] = mapped_column(Text)
    fields_json: Mapped[list[dict]] = mapped_column("fields", JSON)
    seed_urls_json: Mapped[list[str]] = mapped_column("seed_urls", JSON)
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_collection_spec_task_version"),)


class CollectionSource(Base):
    __tablename__ = "collection_sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    spec_version_id: Mapped[str] = mapped_column(
        ForeignKey("collection_spec_versions.spec_version_id"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    origin: Mapped[str] = mapped_column(String(32), default="SEED")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    snapshot_id: Mapped[str | None] = mapped_column(nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("spec_version_id", "canonical_url", name="uq_collection_source_spec_canonical_url"),
    )


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.task_run_id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("collection_sources.source_id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    status_code: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_storage_key: Mapped[str] = mapped_column(Text)
    text_storage_key: Mapped[str] = mapped_column(Text)
    bytes_read: Mapped[int] = mapped_column(Integer)
    text_chars: Mapped[int] = mapped_column(Integer)
    text_preview: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionCommit(Base):
    __tablename__ = "extraction_commits"

    extraction_commit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.task_run_id"), index=True)
    spec_version_id: Mapped[str] = mapped_column(
        ForeignKey("collection_spec_versions.spec_version_id"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("page_snapshots.snapshot_id"), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    record_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("task_run_id", "snapshot_id", name="uq_extraction_commit_run_snapshot"),
    )


class Record(Base):
    __tablename__ = "records"

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), index=True)
    task_run_id: Mapped[str] = mapped_column(ForeignKey("task_runs.task_run_id"), index=True)
    spec_version_id: Mapped[str] = mapped_column(
        ForeignKey("collection_spec_versions.spec_version_id"), index=True
    )
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("page_snapshots.snapshot_id"), index=True)
    extraction_commit_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_commits.extraction_commit_id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    data_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    validation_issues: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldEvidence(Base):
    __tablename__ = "field_evidence"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.record_id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("page_snapshots.snapshot_id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    locator_type: Mapped[str] = mapped_column(String(32))
    locator_json: Mapped[dict] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
