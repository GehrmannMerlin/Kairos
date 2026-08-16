"""M-07: SSE 事件基于 domain_events 重放 + 跨用户隔离（DB 层，不依赖真实 stream 服务）。"""

from __future__ import annotations

import asyncio
import threading
from typing import cast

import pytest
from app.api.events import map_domain_event_to_sse, query_task_events
from app.api.routes.events import _event_stream, _parse_last_event_id
from app.domain.models import DomainEvent
from app.infra.db import Base
from app.state.events import append_domain_event
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from starlette.requests import Request


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


@pytest.mark.parametrize(
    ("domain_type", "sse_type"),
    [
        ("task.execution_preflight_blocked", "EXECUTION_PREFLIGHT_BLOCKED"),
        ("discovery.candidates_found", "SOURCE_CANDIDATES_FOUND"),
        ("discovery.expanded", "LINKS_DISCOVERED"),
        ("run.started", "RUN_STARTED"),
        ("run.node_started", "NODE_STARTED"),
        ("run.node_progress", "NODE_PROGRESS"),
        ("run.checkpoint_committed", "CHECKPOINT_COMMITTED"),
        ("run.node_completed", "NODE_COMPLETED"),
        ("run.node_blocked", "NODE_BLOCKED"),
        ("run.node_failed", "NODE_FAILED"),
        ("run.completed", "RUN_COMPLETED"),
        ("run.partially_completed", "RUN_PARTIALLY_COMPLETED"),
        ("run.failed", "RUN_FAILED"),
        ("run.cancelled", "RUN_CANCELLED"),
    ],
)
def test_all_canonical_execution_events_have_exact_sse_names(
    domain_type: str, sse_type: str
) -> None:
    event = DomainEvent(
        id=6,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type=domain_type,
        aggregate_version=1,
        payload={},
        actor_type="system",
    )

    assert map_domain_event_to_sse(event).event_type == sse_type


@pytest.mark.parametrize(
    ("domain_type", "sse_type"),
    [
        ("fetch.started", "FETCH_STARTED"),
        ("fetch.strategy_selected", "FETCH_STRATEGY_SELECTED"),
        ("fetch.escalated", "BROWSER_ESCALATION"),
        ("fetch.credential_required", "CREDENTIAL_REQUIRED"),
        ("fetch.completed", "FETCH_COMPLETED"),
        ("fetch.failed", "FETCH_FAILED"),
        ("extraction.started", "EXTRACTION_STARTED"),
        ("extraction.progress", "EXTRACTION_PROGRESS"),
        ("extraction.llm_fallback_used", "LLM_FALLBACK_USED"),
        ("extraction.rule_promoted", "RULE_PROMOTED"),
        ("extraction.completed", "EXTRACTION_COMPLETED"),
        ("extraction.failed", "EXTRACTION_FAILED"),
        ("normalize.completed", "NORMALIZE_COMPLETED"),
        ("validation.started", "VALIDATION_STARTED"),
        ("validation.progress", "VALIDATION_PROGRESS"),
        ("validation.dedupe_completed", "DEDUPE_COMPLETED"),
        ("validation.completed", "VALIDATION_COMPLETED"),
        ("record.approved", "RECORD_APPROVED"),
        ("record.rejected", "RECORD_REJECTED"),
        ("record.edited", "RECORD_EDITED"),
        ("record.reevaluate_requested", "RECORD_REEVALUATE_REQUESTED"),
        ("record.approved_batch", "RECORD_APPROVED_BATCH"),
        ("record.rejected_batch", "RECORD_REJECTED_BATCH"),
    ],
)
def test_existing_execution_event_sse_names_remain_compatible(
    domain_type: str, sse_type: str
) -> None:
    event = DomainEvent(
        id=7,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type=domain_type,
        aggregate_version=1,
        payload={},
        actor_type="system",
    )

    assert map_domain_event_to_sse(event).event_type == sse_type


def test_cross_user_isolation(db, user, user2) -> None:
    task_id = 11
    _seed_events(db, user.id, task_id)
    # 另一个用户不能通过 cursor 查询到该 task 事件
    # 事件查询要求 task 归属；无归属任务 → 空/404 由 route 层保证。此处验证 mapper 不含他人数据。
    assert query_task_events(db, user2.id, task_id, after_id=0) == []


def test_sse_replays_canonical_node_events_after_cursor(db, user) -> None:
    task_id = 17
    for event_type in (
        "run.node_started",
        "run.node_completed",
        "run.checkpoint_committed",
        "run.node_started",
    ):
        append_domain_event(
            db,
            user_id=user.id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type=event_type,
            aggregate_version=1,
            payload={
                "schema_version": 1,
                "task_id": task_id,
                "node_id": "n3",
                "node_type": "fetch",
                "state": "RUNNING",
            },
            actor_type="system",
        )
    db.commit()
    all_events = query_task_events(db, user.id, task_id, after_id=0)

    replay = query_task_events(db, user.id, task_id, after_id=all_events[0].id)
    mapped = [map_domain_event_to_sse(event) for event in replay]

    assert [event.event_type for event in mapped] == [
        "NODE_COMPLETED",
        "CHECKPOINT_COMMITTED",
        "NODE_STARTED",
    ]
    assert [event.event_id for event in mapped] == sorted(event.event_id for event in mapped)


def test_sse_payload_is_projected_from_explicit_allowlist() -> None:
    ev = DomainEvent(
        id=18,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type="run.node_failed",
        aggregate_version=3,
        payload={
            "schema_version": 1,
            "task_id": 9,
            "node_id": "n-fetch",
            "node_type": "fetch",
            "attempt": 2,
            "state": "FAILED",
            "reason_code": "NETWORK_TIMEOUT",
            "safe_message": "network request timed out",
            "private_note": "sensitive-value",
            "url": "https://secret.example/path",
            "unexpected": "must-not-pass-through",
        },
        actor_type="system",
    )

    mapped = map_domain_event_to_sse(ev)

    assert mapped.event_type == "NODE_FAILED"
    assert mapped.payload == {
        "schema_version": 1,
        "task_id": 9,
        "node_id": "n-fetch",
        "node_type": "fetch",
        "attempt": 2,
        "state": "FAILED",
        "reason_code": "NETWORK_TIMEOUT",
        "safe_message": "network request timed out",
    }


def test_sse_payload_recursively_projects_structured_fields() -> None:
    event = DomainEvent(
        id=19,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type="discovery.candidates_found",
        aggregate_version=1,
        payload={
            "candidate_sites": 2,
            "candidates": [
                {
                    "candidate_id": 31,
                    "rank": 1,
                    "score": 0.98,
                    "counts": {"discovered": 4, "token": "nested-secret"},
                    "evidence_refs": [71, "72", {"id": 73, "url": "https://secret"}],
                    "url": "https://private.example/path",
                    "authorization": {"Bearer": "secret-token"},
                    "chain_of_thought": ["hidden reasoning"],
                },
                [
                    {"candidate_id": 99, "credential": "deep-secret"},
                    {"header": "Authorization: secret"},
                ],
            ],
            "evidence_refs": [7, "8", {"id": 9, "secret": "must-not-escape"}],
        },
        actor_type="system",
    )

    mapped = map_domain_event_to_sse(event)

    assert mapped.payload == {
        "candidate_sites": 2,
        "candidates": [
            {
                "candidate_id": 31,
                "rank": 1,
                "score": 0.98,
                "counts": {"discovered": 4},
                "evidence_refs": [71, 72, 73],
            }
        ],
        "evidence_refs": [7, 8, 9],
    }
    serialized = mapped.model_dump_json()
    for forbidden in (
        "private.example",
        "secret-token",
        "deep-secret",
        "Authorization",
        "hidden reasoning",
    ):
        assert forbidden not in serialized


def test_discovery_expanded_preserves_typed_numeric_producer_facts() -> None:
    event = DomainEvent(
        id=20,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type="discovery.expanded",
        aggregate_version=1,
        payload={
            "seeds": 3,
            "added": 19,
            "blocked": 4,
            "cross_domain_hints": 2,
            "query": "must-not-be-added-by-accident",
        },
        actor_type="system",
    )

    mapped = map_domain_event_to_sse(event)

    assert mapped.event_type == "LINKS_DISCOVERED"
    assert mapped.payload == {
        "seeds": 3,
        "added": 19,
        "blocked": 4,
        "cross_domain_hints": 2,
    }


def test_sse_projection_drops_oversized_or_malformed_nested_ids() -> None:
    event = DomainEvent(
        id=21,
        user_id=1,
        aggregate_type="task",
        aggregate_id=9,
        event_type="run.node_completed",
        aggregate_version=1,
        payload={
            "evidence_refs": [
                7,
                "9" * 10_000,
                "not-an-id",
                {"id": "8", "token": "nested-secret"},
            ]
        },
        actor_type="system",
    )

    mapped = map_domain_event_to_sse(event)

    assert mapped.payload == {"evidence_refs": [7, 8]}


def test_last_event_id_header_takes_precedence_over_query_cursor() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/tasks/1",
            "headers": [(b"last-event-id", b"21")],
        }
    )

    assert _parse_last_event_id(request, "13") == 21


@pytest.mark.parametrize(
    "value",
    [
        "-1",
        " 7",
        "7 ",
        "１",
        "not-a-cursor",
        "9" * 5_000,
        str(2**63),
    ],
)
def test_invalid_last_event_id_header_is_rejected_without_query_fallback(value: str) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/tasks/1",
            "headers": [(b"last-event-id", value.encode())],
        }
    )

    with pytest.raises(HTTPException) as raised:
        _parse_last_event_id(request, "13")

    assert raised.value.status_code == 400


@pytest.mark.parametrize("value", ["-1", " 7", "１", "bad", "9" * 5_000, str(2**63)])
def test_invalid_query_cursor_is_rejected(value: str) -> None:
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/events/tasks/1", "headers": []}
    )

    with pytest.raises(HTTPException) as raised:
        _parse_last_event_id(request, value)

    assert raised.value.status_code == 400


def test_cursor_accepts_signed_bigint_boundaries() -> None:
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/events/tasks/1", "headers": []}
    )

    assert _parse_last_event_id(request, "0") == 0
    assert _parse_last_event_id(request, str(2**63 - 1)) == 2**63 - 1


def test_replay_cursor_does_not_repeat_delivered_events(db, user) -> None:
    task_id = 23
    _seed_events(db, user.id, task_id)
    first_replay = query_task_events(db, user.id, task_id, after_id=0)

    second_replay = query_task_events(
        db,
        user.id,
        task_id,
        after_id=first_replay[-1].id,
    )

    assert second_replay == []


class _FakeStreamMetrics:
    def __init__(self) -> None:
        self.connection_deltas: list[int] = []
        self.replay_counts: list[int] = []

    def change_sse_connections(self, *, delta: int) -> None:
        self.connection_deltas.append(delta)

    def record_sse_replay(self, *, count: int) -> None:
        self.replay_counts.append(count)


@pytest.mark.asyncio
async def test_sse_stream_decrements_connection_metric_when_closed(db, user) -> None:
    task_id = 29
    _seed_events(db, user.id, task_id)
    metrics = _FakeStreamMetrics()
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    stream = _event_stream(
        session_factory=factory,
        user_id=user.id,
        task_id=task_id,
        cursor=0,
        metrics=metrics,
        poll_interval=0,
    )

    first = await anext(stream)
    await stream.aclose()

    assert "id:" in first
    assert metrics.replay_counts == [3]
    assert metrics.connection_deltas == [1, -1]


def _seed_stream_history(factory, *, count: int, task_id: int = 41, user_id: int = 7) -> None:
    session = factory()
    try:
        session.add_all(
            [
                DomainEvent(
                    user_id=user_id,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    event_type="run.node_progress",
                    aggregate_version=index,
                    payload={"node_id": "n1", "attempt": 1, "state": "RUNNING"},
                )
                for index in range(1, count + 1)
            ]
        )
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_sse_stream_releases_small_pool_connections_between_yields(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sse-pool.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
    )
    Base.metadata.create_all(engine)
    pool = cast(QueuePool, engine.pool)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _seed_stream_history(factory, count=2)
    query_threads: list[int] = []

    @sqlalchemy_event.listens_for(engine, "before_cursor_execute")
    def _record_query_thread(*_args) -> None:
        query_threads.append(threading.get_ident())

    metrics_a = _FakeStreamMetrics()
    metrics_b = _FakeStreamMetrics()
    stream_a = _event_stream(
        session_factory=factory,
        user_id=7,
        task_id=41,
        cursor=0,
        metrics=metrics_a,
        poll_interval=0,
    )
    stream_b = _event_stream(
        session_factory=factory,
        user_id=7,
        task_id=41,
        cursor=0,
        metrics=metrics_b,
        poll_interval=0,
    )
    event_loop_thread = threading.get_ident()

    try:
        assert "id:" in await asyncio.wait_for(anext(stream_a), timeout=1)
        assert pool.checkedout() == 0
        assert "id:" in await asyncio.wait_for(anext(stream_b), timeout=1)
        assert pool.checkedout() == 0
        assert query_threads and all(thread_id != event_loop_thread for thread_id in query_threads)
    finally:
        await stream_a.aclose()
        await stream_b.aclose()


@pytest.mark.asyncio
async def test_sse_large_replay_uses_multiple_bounded_ordered_pages(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sse-pages.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    Base.metadata.create_all(engine)
    pool = cast(QueuePool, engine.pool)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _seed_stream_history(factory, count=205)
    selects: list[str] = []

    @sqlalchemy_event.listens_for(engine, "before_cursor_execute")
    def _capture_select(_conn, _cursor, statement, *_args) -> None:
        if "domain_events" in statement and statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement.upper())

    metrics = _FakeStreamMetrics()
    stream = _event_stream(
        session_factory=factory,
        user_id=7,
        task_id=41,
        cursor=0,
        metrics=metrics,
        poll_interval=0,
    )
    delivered: list[int] = []
    try:
        for _ in range(201):
            chunk = await asyncio.wait_for(anext(stream), timeout=1)
            delivered.append(int(chunk.splitlines()[0].removeprefix("id: ")))
            assert pool.checkedout() == 0
    finally:
        await stream.aclose()

    assert delivered == list(range(1, 202))
    assert len(selects) >= 2
    assert all("LIMIT" in statement for statement in selects)
    assert max(metrics.replay_counts) < 205
