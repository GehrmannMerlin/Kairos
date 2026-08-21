"""Execution timeline SSE 流（GET /tasks/{task_id}/execution/timeline/stream）。

只读投影：回放冻结 replay_through_id 内的已提交事件 → 2s 轮询活区 → keepalive。
事件源与 REST /execution/timeline 同一查询（ExecutionRepository.events_after），
经 TimelineMapper 输出富 TimelineEvent。owner-safe；不触碰 Workflow/Temporal。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from app.execution.contracts import TimelineEvent
from app.execution.repository import ExecutionRepository
from app.execution.timeline import TimelineMapper
from app.observability.execution_metrics import get_execution_metrics

_SSE_PAGE_SIZE = 200


def _format_timeline_sse(event: TimelineEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: timeline\ndata: {data}\n\n"


def _load_timeline_page(
    session_factory,
    user_id: int,
    task_id: int,
    cursor: int,
    through_id: int | None,
) -> list[TimelineEvent]:
    db = session_factory()
    try:
        events = ExecutionRepository(db).events_after(
            user_id=user_id,
            task_id=task_id,
            after_id=cursor,
            limit=_SSE_PAGE_SIZE,
            through_id=through_id,
        )
        return [TimelineMapper.to_timeline_event(ev) for ev in events]
    finally:
        db.rollback()
        db.close()


def _load_max_timeline_event_id(session_factory, user_id: int, task_id: int) -> int:
    db = session_factory()
    try:
        return ExecutionRepository(db).max_event_id(user_id=user_id, task_id=task_id)
    finally:
        db.rollback()
        db.close()


async def timeline_stream(
    *,
    session_factory,
    user_id: int,
    task_id: int,
    cursor: int,
    poll_interval: float = 2.0,
) -> AsyncGenerator[str, None]:
    metrics = get_execution_metrics()
    metrics.change_sse_connections(delta=1)
    try:
        replay_through_id = await asyncio.to_thread(
            _load_max_timeline_event_id,
            session_factory,
            user_id,
            task_id,
        )
        replayed_any = False
        while cursor < replay_through_id:
            page = await asyncio.to_thread(
                _load_timeline_page,
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
                yield _format_timeline_sse(event)
                cursor = event.event_id
        if not replayed_any:
            metrics.record_sse_replay(count=0)

        while True:
            page = await asyncio.to_thread(
                _load_timeline_page,
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
                    yield _format_timeline_sse(event)
                    cursor = event.event_id
                continue
            # No event list survives the poll boundary.
            yield ": ping\n\n"
            await asyncio.sleep(poll_interval)
    finally:
        metrics.change_sse_connections(delta=-1)
