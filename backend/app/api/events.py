"""SSE 任务事件：基于 domain_events 的稳定 typed schema + 重放查询。

SSE 不是业务状态源；只推送用户重要事件（D-039）。cursor = domain_events.id，
断线后 Last-Event-ID 重放不会丢状态。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.domain.models import DomainEvent

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
}


class SSETaskEvent(BaseModel):
    event_id: int
    event_type: str
    task_id: int
    run_id: int | None = None
    occurred_at: datetime
    payload: dict[str, Any]


def query_task_events(db: Any, user_id: int, task_id: int, after_id: int) -> list[DomainEvent]:
    return list(
        db.scalars(
            select(DomainEvent)
            .where(
                DomainEvent.user_id == user_id,
                DomainEvent.aggregate_type == "task",
                DomainEvent.aggregate_id == task_id,
                DomainEvent.id > after_id,
            )
            .order_by(DomainEvent.id)
        )
    )


def map_domain_event_to_sse(ev: DomainEvent) -> SSETaskEvent:
    return SSETaskEvent(
        event_id=ev.id,
        event_type=_EVENT_TYPE_MAP.get(ev.event_type, "TASK_STATE_CHANGED"),
        task_id=ev.aggregate_id,
        run_id=ev.run_id,
        occurred_at=ev.occurred_at or datetime.now(UTC),
        payload=ev.payload or {},
    )
