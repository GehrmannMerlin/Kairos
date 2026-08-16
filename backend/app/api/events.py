"""SSE 任务事件：基于 domain_events 的稳定 typed schema + 重放查询。

SSE 不是业务状态源；只推送用户重要事件（D-039）。cursor = domain_events.id，
断线后 Last-Event-ID 重放不会丢状态。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, or_, select

from app.domain.models import DomainEvent, Record

# domain_events.event_type -> SSE event_type（同一语义，不造第二套名称）
_EVENT_TYPE_MAP = {
    "task.submit": "TASK_STATE_CHANGED",
    "task.start": "TASK_STATE_CHANGED",
    "task.spec_confirmed": "TASK_STATE_CHANGED",
    "task.pause": "TASK_PAUSE_REQUESTED",
    "task.mark_paused": "TASK_PAUSED",
    "task.resume": "TASK_RESUMED",
    "task.cancel": "TASK_CANCEL_REQUESTED",
    "task.mark_cancelled": "TASK_CANCELLED",
    "task.complete": "TASK_COMPLETED",
    "task.mark_partial": "TASK_PARTIALLY_COMPLETED",
    "task.fail": "TASK_FAILED",
    "task.mark_waiting_approval": "APPROVAL_REQUIRED",
    "approval.requested": "APPROVAL_REQUIRED",
    "approval.approved": "APPROVAL_APPROVED",
    "approval.rejected": "APPROVAL_REJECTED",
    "approval.expired": "APPROVAL_EXPIRED",
    "approval.revoked": "APPROVAL_REVOKED",
    "approval.consumed": "APPROVAL_CONSUMED",
    # M-10 fetch 重要事件（D-039：只推用户重要事件，非每 URL 细粒度）
    "fetch.started": "FETCH_STARTED",
    "fetch.strategy_selected": "FETCH_STRATEGY_SELECTED",
    "fetch.escalated": "BROWSER_ESCALATION",
    "fetch.credential_required": "CREDENTIAL_REQUIRED",
    "fetch.completed": "FETCH_COMPLETED",
    "fetch.failed": "FETCH_FAILED",
    # M-11 extraction 重要事件（D-039：只推聚合事件，不逐字段推 Evidence snippet）
    "extraction.started": "EXTRACTION_STARTED",
    "extraction.progress": "EXTRACTION_PROGRESS",
    "extraction.llm_fallback_used": "LLM_FALLBACK_USED",
    "extraction.rule_promoted": "RULE_PROMOTED",
    "extraction.completed": "EXTRACTION_COMPLETED",
    "extraction.failed": "EXTRACTION_FAILED",
    "normalize.completed": "NORMALIZE_COMPLETED",
    # M-12 validation/quality 聚合事件（D-039：只推聚合事件，不逐 Record 推）
    "validation.started": "VALIDATION_STARTED",
    "validation.progress": "VALIDATION_PROGRESS",
    "validation.dedupe_completed": "DEDUPE_COMPLETED",
    "validation.completed": "VALIDATION_COMPLETED",
    # M-13 record review 事件（D-040/D-061：数据页增量刷新）
    "record.approved": "RECORD_APPROVED",
    "record.rejected": "RECORD_REJECTED",
    "record.edited": "RECORD_EDITED",
    "record.reevaluate_requested": "RECORD_REEVALUATE_REQUESTED",
    "record.approved_batch": "RECORD_APPROVED_BATCH",
    "record.rejected_batch": "RECORD_REJECTED_BATCH",
    # Canonical persisted execution facts (Task 8).
    "task.execution_preflight_blocked": "EXECUTION_PREFLIGHT_BLOCKED",
    "discovery.candidates_found": "SOURCE_CANDIDATES_FOUND",
    "discovery.expanded": "LINKS_DISCOVERED",
    "run.started": "RUN_STARTED",
    "run.node_started": "NODE_STARTED",
    "run.node_progress": "NODE_PROGRESS",
    "run.checkpoint_committed": "CHECKPOINT_COMMITTED",
    "run.node_completed": "NODE_COMPLETED",
    "run.node_blocked": "NODE_BLOCKED",
    "run.node_failed": "NODE_FAILED",
    "run.completed": "RUN_COMPLETED",
    "run.partially_completed": "RUN_PARTIALLY_COMPLETED",
    "run.failed": "RUN_FAILED",
    "run.cancelled": "RUN_CANCELLED",
}

_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "task_id",
        "run_id",
        "spec_version",
        "plan_version",
        "node_id",
        "node_type",
        "attempt",
        "state",
        "status",
        "transition",
        "command",
        "from_state",
        "to_state",
        "reason",
        "reason_code",
        "error_code",
        "safe_message",
        "checkpoint_id",
        "seed_count",
        "counts",
        "timestamps",
        "candidate_sites",
        "candidates",
        "discovered_count",
        "expanded_count",
        "provider",
        "tool",
        "strategy",
        "retry_count",
        "model",
        "field",
        "tokens_in",
        "tokens_out",
        "duration_ms",
        "partition",
        "data_version",
        "record_id",
        "record_ids",
        "review_type",
        "success_count",
        "failure_count",
        "validation_status",
        "approval_id",
        "approval_type",
        "risk_level",
        "decision",
        "snapshot_id",
        "evidence_refs",
        "trace_id",
        "outcome_code",
        "waiting_reason_code",
    }
)
_TIMESTAMP_FIELDS = frozenset({"started_at", "finished_at", "committed_at"})
_COUNT_FIELDS = frozenset(
    {
        "fetched",
        "browser_pending",
        "failed",
        "discovered",
        "extracted",
        "normalized",
        "deduplicated",
        "validated",
        "records",
        "artifacts",
        "eligible",
        "terminal",
        "passed",
        "needs_review",
        "rejected",
    }
)


class SSETaskEvent(BaseModel):
    event_id: int
    event_type: str
    task_id: int
    run_id: int | None = None
    occurred_at: datetime
    payload: dict[str, Any]


def query_task_events(db: Any, user_id: int, task_id: int, after_id: int) -> list[DomainEvent]:
    # task.* 事件 + 本 task 的 record.* 事件（通过 records 表关联，record 事件无 task_id 列）
    record_ids = select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
    return list(
        db.scalars(
            select(DomainEvent)
            .where(
                DomainEvent.user_id == user_id,
                DomainEvent.id > after_id,
                or_(
                    and_(
                        DomainEvent.aggregate_type == "task",
                        DomainEvent.aggregate_id == task_id,
                    ),
                    and_(
                        DomainEvent.aggregate_type == "record",
                        DomainEvent.aggregate_id.in_(record_ids),
                    ),
                ),
            )
            .order_by(DomainEvent.id)
        )
    )


def map_domain_event_to_sse(ev: DomainEvent) -> SSETaskEvent:
    # record 事件无 task_id 列，task_id 由 ReviewService 写入 payload
    if ev.aggregate_type == "record":
        task_id = int(ev.payload.get("task_id") or ev.aggregate_id)
    else:
        task_id = ev.aggregate_id
    return SSETaskEvent(
        event_id=ev.id,
        event_type=_EVENT_TYPE_MAP.get(ev.event_type, "TASK_STATE_CHANGED"),
        task_id=task_id,
        run_id=ev.run_id,
        occurred_at=ev.occurred_at or datetime.now(UTC),
        payload=_project_payload(ev.payload),
    )


def _project_payload(payload: Any) -> dict[str, Any]:
    """Build a safe copy; DomainEvent payload is never exposed wholesale."""
    if not isinstance(payload, dict):
        return {}
    projected: dict[str, Any] = {}
    for key in _PAYLOAD_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key == "timestamps":
            if isinstance(value, dict):
                projected[key] = {k: value[k] for k in _TIMESTAMP_FIELDS if k in value}
            continue
        if key == "counts":
            if isinstance(value, dict):
                projected[key] = {k: value[k] for k in _COUNT_FIELDS if k in value}
            continue
        projected[key] = value
    return projected
