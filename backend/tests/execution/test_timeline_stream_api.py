"""M-14 execution timeline stream SSE API（GET /tasks/{task_id}/execution/timeline/stream）。

验证（A-Lite 紧凑套件）：
1. ?after_id 重放富 TimelineEvent（TimelineMapper 投影，不含原始 payload secret）。
2. Last-Event-ID 优先于 after_id（路由级 + 非法 header 不回落 query）。
3. 2s 轮询窗口内实时推送新事件 + 空闲 keepalive（: ping）。
4. owner 隔离：跨用户 → 404。
5. payload allowlist：api_key/cookie/token/Bearer 不进入流。
6. 回放冻结 replay_through_id：回放期间新增 id < 边界的事件不在活区重复推送。
7. 非法游标（非数字/超 2^63-1）→ 400。
8. 大批量重放分页（_SSE_PAGE_SIZE=200）。
9. 断开连接连接指标递减。

注：TestClient 的 ASGI transport 会缓冲完整 body，无法增量读取无限 SSE 流，
因此流语义（replay/live/keepalive/allowlist/freeze/paging/metrics）仿照
tests/api/test_task_events.py 在生成器/路由 body_iterator 层面验证；HTTP 层只
验证不读取 body 的路由行为（404/400/header 优先级拒绝）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from app.api.routes.execution import get_timeline_stream
from app.domain.models import DomainEvent, Run
from app.domain.repository import TaskRepository
from app.execution.repository import ExecutionRepository
from app.execution.timeline_stream import timeline_stream
from app.infra.db import Base
from app.state.events import append_domain_event
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from starlette.requests import Request

_COOKIE_NAME = "kairos_session"


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(_COOKIE_NAME)
    assert token, "register should set a session cookie"
    return {"Cookie": f"{_COOKIE_NAME}={token}"}


def _parse_sse_block(block: str) -> dict:
    parsed: dict = {}
    for line in block.splitlines():
        if line.startswith("id: "):
            parsed["event_id"] = int(line[4:])
        elif line.startswith("event: "):
            parsed["event_type"] = line[7:]
        elif line.startswith("data: "):
            parsed["data"] = json.loads(line[6:])
    return parsed


def _seed_stream_case(db, user_id: int, task_id: int) -> int:
    """Seed run.started(id=10) + run.node_started(id=11, secret payload). Returns run_id."""
    run = Run(user_id=user_id, task_id=task_id, spec_version=1, plan_version=1, state="RUNNING")
    db.add(run)
    db.flush()
    db.add_all(
        [
            DomainEvent(
                id=10,
                user_id=user_id,
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.started",
                aggregate_version=1,
                payload={"state": "RUNNING"},
                run_id=run.id,
            ),
            DomainEvent(
                id=11,
                user_id=user_id,
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.node_started",
                aggregate_version=2,
                payload={
                    "node_id": "n1",
                    "node_type": "source_search",
                    "attempt": 1,
                    "status": "RUNNING",
                    "api_key": "SK-SECRET",
                    "cookie": "c=secret",
                    "authorization": "Bearer secret-token",
                    "token": "t=secret",
                },
                run_id=run.id,
                node_run_id=42,
            ),
        ]
    )
    db.commit()
    return run.id


class _StreamCase:
    def __init__(
        self,
        client: TestClient,
        auth: dict[str, str],
        *,
        task_id: int,
    ) -> None:
        self.client = client
        self.auth = auth
        self.task = SimpleNamespace(id=task_id)


@pytest.fixture()
def stream_case(client: dict) -> _StreamCase:
    c, factory = client["client"], client["factory"]
    owner = _register(c, "timeline-owner@example.com")["user"]
    auth = _auth(c)
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=owner["id"], title="stream", task_type="directed"
        )
        task_id = task.id
    finally:
        session.close()
    return _StreamCase(client=c, auth=auth, task_id=task_id)


@pytest.fixture()
def other_user(client: dict) -> dict[str, str]:
    c = client["client"]
    _register(c, "timeline-intruder@example.com")
    return _auth(c)


class _FakeStreamMetrics:
    def __init__(self) -> None:
        self.connection_deltas: list[int] = []
        self.replay_counts: list[int] = []

    def change_sse_connections(self, *, delta: int) -> None:
        self.connection_deltas.append(delta)

    def record_sse_replay(self, *, count: int) -> None:
        self.replay_counts.append(count)


# ---------------------------------------------------------------- HTTP route tests

def test_stream_owner_isolated(stream_case: _StreamCase, other_user: dict[str, str]) -> None:
    resp = stream_case.client.get(
        f"/api/tasks/{stream_case.task.id}/execution/timeline/stream",
        headers=other_user,
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "after_id",
    ["-1", "not-a-number", "12x", "１", str(2**63)],
)
def test_stream_invalid_cursor_returns_400(stream_case: _StreamCase, after_id: str) -> None:
    resp = stream_case.client.get(
        f"/api/tasks/{stream_case.task.id}/execution/timeline/stream?after_id={after_id}",
        headers=stream_case.auth,
    )
    assert resp.status_code == 400


def test_stream_invalid_last_event_id_header_rejects_without_query_fallback(
    stream_case: _StreamCase,
) -> None:
    # Header is parsed first: an invalid header must 400 even when after_id is valid.
    resp = stream_case.client.get(
        f"/api/tasks/{stream_case.task.id}/execution/timeline/stream?after_id=5",
        headers={**stream_case.auth, "Last-Event-ID": "not-a-cursor"},
    )
    assert resp.status_code == 400


# ------------------------------------------------ route-level (drive body_iterator)

def _stream_route(request: Request, db, user, task_id: int, after_id: str | None = None):
    resp = get_timeline_stream(
        task_id=task_id, request=request, after_id=after_id, user=user, db=db
    )
    assert resp.body_iterator is not None
    return resp.body_iterator


@pytest.mark.asyncio
async def test_stream_replays_rich_timeline_events_after_cursor(db, user_a, task_a) -> None:
    _seed_stream_case(db, user_a.id, task_a.id)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/tasks/{task_a.id}/execution/timeline/stream",
            "headers": [],
        }
    )
    iterator = _stream_route(request, db, user_a, task_a.id, after_id="10")
    try:
        chunk = await asyncio.wait_for(anext(iterator), timeout=2)
    finally:
        await iterator.aclose()

    event = _parse_sse_block(chunk)
    assert event["event_id"] == 11
    assert event["event_type"] == "timeline"
    assert event["data"]["node_id"] == "n1"
    assert event["data"]["stage"] == "source_discovery"
    assert event["data"]["status"] == "RUNNING"
    assert event["data"]["node_run_id"] is not None


@pytest.mark.asyncio
async def test_stream_last_event_id_precedence(db, user_a, task_a) -> None:
    _seed_stream_case(db, user_a.id, task_a.id)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/tasks/{task_a.id}/execution/timeline/stream",
            "headers": [(b"last-event-id", b"10")],
        }
    )
    # after_id=7 is valid but the header cursor 10 must win → replay starts after 10.
    iterator = _stream_route(request, db, user_a, task_a.id, after_id="7")
    try:
        chunk = await asyncio.wait_for(anext(iterator), timeout=2)
    finally:
        await iterator.aclose()

    event = _parse_sse_block(chunk)
    assert event["event_id"] == 11


@pytest.mark.asyncio
async def test_stream_payload_allowlist_no_secret(db, user_a, task_a) -> None:
    _seed_stream_case(db, user_a.id, task_a.id)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/tasks/{task_a.id}/execution/timeline/stream",
            "headers": [],
        }
    )
    iterator = _stream_route(request, db, user_a, task_a.id, after_id="0")
    chunks: list[str] = []
    try:
        for _ in range(2):  # ids 10 + 11
            chunks.append(await asyncio.wait_for(anext(iterator), timeout=2))
    finally:
        await iterator.aclose()

    raw = "\n\n".join(chunks)
    assert "SK-SECRET" not in raw
    assert "Bearer" not in raw


# ---------------------------------------------------------- generator-level semantics

@pytest.mark.asyncio
async def test_stream_live_appends_and_keepalive(db, user_a, task_a, monkeypatch) -> None:
    run_id = _seed_stream_case(db, user_a.id, task_a.id)
    metrics = _FakeStreamMetrics()
    monkeypatch.setattr("app.execution.timeline_stream.get_execution_metrics", lambda: metrics)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    stream = timeline_stream(
        session_factory=factory,
        user_id=user_a.id,
        task_id=task_a.id,
        cursor=0,
        poll_interval=0.05,
    )

    chunks: list[str] = []
    try:
        chunks.append(await anext(stream))  # id 10
        chunks.append(await anext(stream))  # id 11
        chunks.append(await anext(stream))  # keepalive (live page empty)
        append_domain_event(
            db,
            user_id=user_a.id,
            aggregate_type="task",
            aggregate_id=task_a.id,
            event_type="run.node_completed",
            aggregate_version=3,
            payload={"node_id": "n1", "node_type": "fetch", "attempt": 1, "status": "COMPLETED"},
            actor_type="system",
            run_id=run_id,
            node_run_id=42,
        )
        db.commit()
        live_events = ExecutionRepository(db).events_after(
            user_id=user_a.id, task_id=task_a.id, after_id=11, limit=200
        )
        live_id = live_events[0].id
        live = await asyncio.wait_for(anext(stream), timeout=2)  # within poll window
        chunks.append(live)
    finally:
        await stream.aclose()

    assert any(": ping" in chunk for chunk in chunks)
    assert f"id: {live_id}" in live
    assert "节点已完成" in live  # TimelineEvent summary for run.node_completed


def _seed_task_events(db, user_id: int, task_id: int) -> None:
    for index, event_type in enumerate(["task.pause", "task.mark_paused", "task.resume"], start=1):
        append_domain_event(
            db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type=event_type,
            aggregate_version=index,
            payload={"command": event_type},
            actor_type="user",
            actor_id=user_id,
        )
    db.commit()


@pytest.mark.asyncio
async def test_stream_replay_freezes_initial_boundary_before_live_events(
    db, user_a, task_a, monkeypatch
) -> None:
    task_id = task_a.id
    _seed_task_events(db, user_a.id, task_id)
    initial = ExecutionRepository(db).events_after(
        user_id=user_a.id, task_id=task_id, after_id=0, limit=200
    )
    metrics = _FakeStreamMetrics()
    monkeypatch.setattr("app.execution.timeline_stream._SSE_PAGE_SIZE", 2)
    monkeypatch.setattr("app.execution.timeline_stream.get_execution_metrics", lambda: metrics)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    stream = timeline_stream(
        session_factory=factory,
        user_id=user_a.id,
        task_id=task_id,
        cursor=0,
        poll_interval=0,
    )

    delivered: list[int] = []
    try:
        first = await anext(stream)
        delivered.append(int(first.splitlines()[0].removeprefix("id: ")))
        append_domain_event(
            db,
            user_id=user_a.id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="run.node_progress",
            aggregate_version=4,
            payload={"node_id": "live", "state": "RUNNING"},
            actor_type="system",
        )
        db.commit()
        live_id = ExecutionRepository(db).events_after(
            user_id=user_a.id, task_id=task_id, after_id=initial[-1].id, limit=200
        )[0].id
        for _ in range(3):
            chunk = await anext(stream)
            delivered.append(int(chunk.splitlines()[0].removeprefix("id: ")))
    finally:
        await stream.aclose()

    assert delivered == [event.id for event in initial] + [live_id]
    assert metrics.replay_counts == [2, 1]


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
async def test_stream_large_replay_uses_multiple_bounded_ordered_pages(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'timeline-pages.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _seed_stream_history(factory, count=205)
    metrics = _FakeStreamMetrics()
    monkeypatch.setattr("app.execution.timeline_stream.get_execution_metrics", lambda: metrics)
    stream = timeline_stream(
        session_factory=factory, user_id=7, task_id=41, cursor=0, poll_interval=0
    )

    delivered: list[int] = []
    try:
        for _ in range(201):
            chunk = await asyncio.wait_for(anext(stream), timeout=1)
            delivered.append(int(chunk.splitlines()[0].removeprefix("id: ")))
    finally:
        await stream.aclose()

    assert delivered == list(range(1, 202))
    assert max(metrics.replay_counts) < 205


@pytest.mark.asyncio
async def test_stream_decrements_connection_metric_when_closed(
    db, user_a, task_a, monkeypatch
) -> None:
    metrics = _FakeStreamMetrics()
    monkeypatch.setattr("app.execution.timeline_stream.get_execution_metrics", lambda: metrics)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    stream = timeline_stream(
        session_factory=factory,
        user_id=user_a.id,
        task_id=task_a.id,
        cursor=0,
        poll_interval=0,
    )

    first = await anext(stream)
    await stream.aclose()

    assert first == ": ping\n\n"
    assert metrics.replay_counts == [0]
    assert metrics.connection_deltas == [1, -1]
