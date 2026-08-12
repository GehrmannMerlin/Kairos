"""M-13 SSE record.* 事件映射 + 任务流重放包含 record 事件。"""

from __future__ import annotations

from app.api.events import map_domain_event_to_sse, query_task_events
from app.domain.models import DomainEvent, Record
from app.state.events import append_domain_event


def test_record_events_map_to_sse() -> None:
    ev = DomainEvent(
        id=101,
        user_id=1,
        aggregate_type="record",
        aggregate_id=5,
        event_type="record.approved",
        aggregate_version=2,
        payload={"task_id": 9, "partition": "passed", "data_version": 2},
    )
    sse = map_domain_event_to_sse(ev)
    assert sse.event_type == "RECORD_APPROVED"
    assert sse.task_id == 9
    assert sse.payload["partition"] == "passed"


def test_unknown_record_event_falls_back() -> None:
    ev = DomainEvent(
        id=102,
        user_id=1, aggregate_type="record", aggregate_id=5,
        event_type="record.something_else", aggregate_version=1,
        payload={"task_id": 9},
    )
    sse = map_domain_event_to_sse(ev)
    assert sse.event_type == "TASK_STATE_CHANGED"
    assert sse.task_id == 9


def test_query_task_events_includes_record_events(db, user_a, task_a) -> None:
    rec = Record(
        user_id=user_a.id, task_id=task_a.id, spec_version=1,
        partition="needs_review", payload={"标题": "x"},
    )
    db.add(rec)
    db.flush()
    append_domain_event(
        db, user_id=user_a.id, aggregate_type="record", aggregate_id=rec.id,
        aggregate_version=1, event_type="record.approved",
        payload={"task_id": task_a.id, "partition": "passed"},
    )
    db.flush()
    events = query_task_events(db, user_a.id, task_a.id, 0)
    assert any(e.event_type == "record.approved" for e in events)
