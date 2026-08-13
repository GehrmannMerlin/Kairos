"""M-07: SSE 事件基于 domain_events 重放 + 跨用户隔离（DB 层，不依赖真实 stream 服务）。"""

from __future__ import annotations

from app.api.events import map_domain_event_to_sse, query_task_events
from app.domain.models import DomainEvent
from app.state.events import append_domain_event


def _seed_events(db, user_id: int, task_id: int) -> None:
    for i, ev in enumerate(["task.pause", "task.mark_paused", "task.resume"], start=1):
        append_domain_event(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type=ev,
            aggregate_version=i,
            payload={"command": ev},
            actor_type="user",
            actor_id=user_id,
        )
    db.commit()


def test_replay_after_cursor(db, user) -> None:
    task_id = 7
    _seed_events(db, user.id, task_id)
    first = query_task_events(db, user.id, task_id, after_id=0)
    assert [e.event_type for e in first] == ["task.pause", "task.mark_paused", "task.resume"]
    after_first = query_task_events(db, user.id, task_id, after_id=first[0].id)
    assert [e.event_type for e in after_first] == ["task.mark_paused", "task.resume"]


def test_sse_mapping() -> None:
    ev = DomainEvent(
        id=5,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type="task.mark_paused",
        aggregate_version=3,
        payload={"command": "mark_paused"},
        actor_type="system",
        actor_id=None,
    )
    sse = map_domain_event_to_sse(ev)
    assert sse.event_type == "TASK_PAUSED"
    assert sse.event_id == 5
    assert sse.task_id == 9
    assert sse.payload["command"] == "mark_paused"


def test_cross_user_isolation(db, user, user2) -> None:
    task_id = 11
    _seed_events(db, user.id, task_id)
    # 另一个用户不能通过 cursor 查询到该 task 事件
    # 事件查询要求 task 归属；无归属任务 → 空/404 由 route 层保证。此处验证 mapper 不含他人数据。
    assert query_task_events(db, user2.id, task_id, after_id=0) == []
