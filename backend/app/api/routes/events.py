"""SSE 任务事件流端点（/api/events/tasks/{task_id}）。

- require_user + owner-safe Task 查询（无权限/不存在 → 404，不泄漏存在性）。
- 连接时按 Last-Event-ID / ?after_id 重放 domain_events，然后实时推送新事件。
- keepalive 只是注释行（: ping），不是 DomainEvent、不占业务 sequence（D-039）。
- 每进程维护连接 registry + 轻量轮询，不引入 Redis。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.api.events import (
    SSETaskEvent,
    map_domain_event_to_sse,
    max_task_event_id,
    query_task_events,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.observability.execution_metrics import get_execution_metrics

router = APIRouter(prefix="/events", tags=["events"])
_MAX_EVENT_ID = 2**63 - 1
_SSE_PAGE_SIZE = 200


class _StreamMetrics(Protocol):
    def record_sse_replay(self, *, count: int) -> None: ...

    def change_sse_connections(self, *, delta: int) -> None: ...


class _SessionFactory(Protocol):
    def __call__(self) -> DbSession: ...


def _parse_event_id(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 < len(value) <= 19:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    cursor = int(value)
    if cursor > _MAX_EVENT_ID:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    return cursor


def _parse_last_event_id(request: Request, after_id: str | None) -> int:
    header = request.headers.get("last-event-id")
    if header is not None:
        return _parse_event_id(header)
    if after_id is not None:
        return _parse_event_id(after_id)
    return 0


def _format_sse(event: SSETaskEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


async def _event_stream(
    *,
    session_factory: _SessionFactory,
    user_id: int,
    task_id: int,
    cursor: int,
    metrics: _StreamMetrics,
    poll_interval: float = 2.0,
) -> AsyncGenerator[str, None]:
    metrics.change_sse_connections(delta=1)
    try:
        replay_through_id = await asyncio.to_thread(
            _load_max_event_id,
            session_factory,
            user_id,
            task_id,
        )
        replayed_any = False
        while cursor < replay_through_id:
            page = await asyncio.to_thread(
                _load_event_page,
                session_factory,
                user_id,
                task_id,
                cursor,
                replay_through_id,
            )
            if not page:
                break
            metrics.record_sse_replay(count=len(page))
            replayed_any = True
            for event in page:
                if event.event_id <= cursor:
                    continue
                yield _format_sse(event)
                cursor = event.event_id
        if not replayed_any:
            metrics.record_sse_replay(count=0)

        while True:
            page = await asyncio.to_thread(
                _load_event_page,
                session_factory,
                user_id,
                task_id,
                cursor,
                None,
            )
            if page:
                for event in page:
                    if event.event_id <= cursor:
                        continue
                    yield _format_sse(event)
                    cursor = event.event_id
                continue
            # No event list survives the poll boundary.
            yield ": ping\n\n"
            await asyncio.sleep(poll_interval)
    finally:
        metrics.change_sse_connections(delta=-1)


def _load_event_page(
    session_factory: _SessionFactory,
    user_id: int,
    task_id: int,
    cursor: int,
    through_id: int | None,
) -> list[SSETaskEvent]:
    db = session_factory()
    try:
        events = query_task_events(
            db,
            user_id=user_id,
            task_id=task_id,
            after_id=cursor,
            limit=_SSE_PAGE_SIZE,
            through_id=through_id,
        )
        return [map_domain_event_to_sse(event) for event in events]
    finally:
        db.rollback()
        db.close()


def _load_max_event_id(
    session_factory: _SessionFactory,
    user_id: int,
    task_id: int,
) -> int:
    db = session_factory()
    try:
        return max_task_event_id(db, user_id=user_id, task_id=task_id)
    finally:
        db.rollback()
        db.close()


@router.get("/tasks/{task_id}")
def task_events(
    task_id: int,
    request: Request,
    after_id: str | None = None,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> StreamingResponse:
    user_id = user.id
    cursor = _parse_last_event_id(request, after_id)
    TaskRepository(db).get_owned(user_id, task_id)  # owner-safe 404
    stream_sessions = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    db.rollback()

    stream = _event_stream(
        session_factory=stream_sessions,
        user_id=user_id,
        task_id=task_id,
        cursor=cursor,
        metrics=get_execution_metrics(),
    )
    return StreamingResponse(stream, media_type="text/event-stream")
