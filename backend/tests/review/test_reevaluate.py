"""M-13 agent_reevaluate：新尝试/事件 + 保留历史（D-042）。"""

from __future__ import annotations

from app.domain.models import DomainEvent, OutboxEvent, Record, RecordReviewAction
from app.review.contracts import RecordReviewCommand, ReviewAction
from app.review.service import ReviewService
from sqlalchemy import select


def _rec(db, user_id: int, task_id: int) -> Record:
    row = Record(
        user_id=user_id,
        task_id=task_id,
        spec_version=1,
        partition="needs_review",
        review_type="low_evidence_confidence",
        payload={"标题": "待重处理", "snapshot_id": 7},
    )
    db.add(row)
    db.flush()
    return row


def test_reevaluate_appends_event_outbox_and_keeps_history(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id)
    svc = ReviewService(db)
    svc.execute(
        user_id=user_a.id,
        record_id=rec.id,
        cmd=RecordReviewCommand(
            action=ReviewAction.AGENT_REEVALUATE, expected_data_version=rec.data_version
        ),
    )

    ev = db.scalar(
        select(DomainEvent).where(
            DomainEvent.aggregate_id == rec.id,
            DomainEvent.event_type == "record.reevaluate_requested",
        )
    )
    assert ev is not None
    assert ev.payload["task_id"] == task_a.id

    ob = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == rec.id, OutboxEvent.event_type == "record.reevaluate"
        )
    )
    assert ob is not None

    # 旧 Record 与 review 历史保留（append-only）
    assert db.get(Record, rec.id) is not None
    act = db.scalar(
        select(RecordReviewAction).where(
            RecordReviewAction.record_id == rec.id,
            RecordReviewAction.action_type == "agent_reevaluate",
        )
    )
    assert act is not None
    assert act.reviewed_by == user_a.id

    # 标记待重算，供后续 run 处理
    assert db.get(Record, rec.id).payload.get("recompute_eligible") is True
