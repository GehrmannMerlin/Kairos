"""OutboxTemporalDispatcher：把 task.* 命令 outbox 事件分发为 Temporal Signal。

优先保证 DB 与 Temporal 最终一致：DB 事务先提交（state+event+outbox），这里再
Signal；失败按 outbox 有界重试，dispatch_key 唯一。Workflow 不存在时（如 QUEUED
直接 cancel）标记 dispatched 为 no-op。
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from app.domain.repository import OutboxRepository

# command -> workflow signal 名称
_TASK_SIGNALS = {
    "task.pause": "pause",
    "task.resume": "resume",
    "task.cancel": "cancel",
}


class OutboxTemporalDispatcher:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def dispatch_pending_for(self, db: Any, *, user_id: int, task_id: int) -> int:
        repo = OutboxRepository(db)
        # 只取本 task 的 pending command 事件（按 aggregate 过滤，避免被其他任务占满 limit）。
        pending = repo.claim_pending_for_aggregate(
            user_id=user_id, aggregate_type="task", aggregate_id=task_id
        )
        sent = 0
        for event in pending:
            signal = _TASK_SIGNALS.get(event.event_type)
            if signal is None:
                repo.mark_dispatched(event)  # 非 command 事件：直接标记，不 Signal
                continue
            workflow_id = f"task-workflow-{task_id}"
            handle = self._client.get_workflow_handle(workflow_id)
            try:
                await handle.signal(signal)
                repo.mark_dispatched(event)
                sent += 1
            except RPCError as exc:
                if exc.status == RPCStatusCode.NOT_FOUND:
                    # QUEUED 直接 cancel 等场景：无 workflow，DB 已反映最终状态
                    repo.mark_dispatched(event)
                else:
                    repo.mark_failed(event)
            except Exception:
                repo.mark_failed(event)
        return sent
