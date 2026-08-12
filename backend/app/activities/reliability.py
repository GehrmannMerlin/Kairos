"""M-16 可靠性 activity：资源等待事件 / task slot heartbeat（DB 副作用放 Activity）。

record_resource_wait 追加 task.resource_waiting + node.resource_waiting DomainEvent，
表达「资源不足是等待不是失败」的等待事实（不做状态转换，Task 保持 QUEUED/RUNNING）。
heartbeat_task_slot 只是延长资源 lease（资源占用事实，非业务 Checkpoint）。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.config import get_settings
from app.infra.deps import get_session_factory
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import capacity_from_settings


@dataclass
class RecordResourceWaitInput:
    task_id: int
    user_id: int
    run_id: int
    waiting_reason: str
    resource_class: str | None = None
    retry_after_seconds: float = 5.0
    attempt: int = 1


@activity.defn
async def record_resource_wait(inp: RecordResourceWaitInput) -> None:
    session = get_session_factory()()
    try:
        from app.state.events import append_domain_event

        payload = {
            "waiting_reason": inp.waiting_reason,
            "resource_class": inp.resource_class,
            "retry_after_seconds": inp.retry_after_seconds,
            "attempt": inp.attempt,
        }
        append_domain_event(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type="task.resource_waiting",
            aggregate_version=0,
            payload=payload,
            actor_type="system",
            run_id=inp.run_id,
        )
        append_domain_event(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type="node.resource_waiting",
            aggregate_version=0,
            payload=payload,
            actor_type="system",
            run_id=inp.run_id,
        )
        session.commit()
    finally:
        session.close()


@dataclass
class HeartbeatTaskSlotInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def heartbeat_task_slot(inp: HeartbeatTaskSlotInput) -> None:
    session = get_session_factory()()
    try:
        ResourceAdmission(
            session, capacity_from_settings(get_settings())
        ).heartbeat_task_slot(user_id=inp.user_id, holder_id=f"run{inp.run_id}")
        session.commit()
    finally:
        session.close()
