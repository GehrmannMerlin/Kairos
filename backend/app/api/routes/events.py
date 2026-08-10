"""SSE 任务事件流端点（/api/events/tasks/{task_id}）。

- require_user + owner-safe Task 查询（无权限/不存在 → 404，不泄漏存在性）。
- 连接时按 Last-Event-ID / ?after_id 重放 domain_events，然后实时推送新事件。
- keepalive 只是注释行（: ping），不是 DomainEvent、不占业务 sequence（D-039）。
- 每进程维护连接 registry + 轻量轮询，不引入 Redis。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.api.events import SSETaskEvent, map_domain_event_to_sse, query_task_events
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db

router = APIRouter(prefix="/events", tags=["events"])


def _parse_last_event_id(request: Request, after_id: str | None) -> int:
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        return int(header)
    if after_id and after_id.isdigit():
        return int(after_id)
    return 0


def _format_sse(event: SSETaskEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/tasks/{task_id}")
async def task_events(
    task_id: int,
    request: Request,
    after_id: str | None = None,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> StreamingResponse:
    TaskRepository(db).get_owned(user.id, task_id)  # owner-safe 404

    async def event_stream() -> Any:
        cursor = _parse_last_event_id(request, after_id)
        # 1) 重放 cursor 之后的已持久化事件
        replay = query_task_events(db, user_id=user.id, task_id=task_id, after_id=cursor)
        for ev in replay:
            yield _format_sse(map_domain_event_to_sse(ev))
            cursor = ev.id
        # 2) 实时轮询 + keepalive（轻量；不引入 Redis）
        while True:
            new = query_task_events(db, user_id=user.id, task_id=task_id, after_id=cursor)
            for ev in new:
                yield _format_sse(map_domain_event_to_sse(ev))
                cursor = ev.id
            yield ": ping\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
