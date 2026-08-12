"""M-13 agent_reevaluate：标记记录待重算 + 追加事件 + 入队 outbox（D-042）。

不覆盖旧历史：旧 Record/FieldEvidence/DomainEvent 全部保留。record.payload 置
recompute_eligible=True（复用 M-12 recompute 标记约定），outbox `record.reevaluate`
由 OutboxTemporalDispatcher 分发为 workflow signal，由后续 run 产生新的执行尝试
（真实重抓/重提取不在 M-13 范围内）。
"""

from __future__ import annotations

from typing import Any

from app.domain.models import Record
from app.review.repository import ReviewRepository
from app.state.events import append_domain_event, enqueue_outbox


def request_reevaluate(
    db: Any,
    *,
    user_id: int,
    record: Record,
    reason: str | None,
    batch_operation_id: str | None = None,
) -> None:
    record.data_version += 1
    payload = dict(record.payload or {})
    payload["recompute_eligible"] = True
    record.payload = payload
    append_domain_event(
        db,
        user_id=user_id,
        aggregate_type="record",
        aggregate_id=record.id,
        aggregate_version=record.data_version,
        event_type="record.reevaluate_requested",
        payload={
            "task_id": record.task_id,
            "record_id": record.id,
            "reason": reason,
            "snapshot_id": record.payload.get("snapshot_id"),
            "data_version": record.data_version,
        },
        actor_type="user",
        actor_id=user_id,
        run_id=record.run_id,
    )
    enqueue_outbox(
        db,
        user_id=user_id,
        aggregate_type="record",
        aggregate_id=record.id,
        event_type="record.reevaluate",
        payload={"record_id": record.id, "task_id": record.task_id, "reason": reason},
        dispatch_key=f"record:{record.id}",
    )
    ReviewRepository(db).create_review_action(
        user_id=user_id,
        task_id=record.task_id,
        record_id=record.id,
        action_type="agent_reevaluate",
        review_type=record.review_type,
        review_reason=record.review_reason,
        batch_operation_id=batch_operation_id,
        reason=reason,
        reviewed_by=user_id,
    )
