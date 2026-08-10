"""M-07: pause/resume/cancel 命令幂等 + 状态机 + outbox 入队。"""

from __future__ import annotations

import pytest
from app.domain.models import OutboxEvent, Task
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.task_commands import TaskCommandService


@pytest.fixture()
def running_task(db, user) -> Task:
    task = TaskRepository(db).create(user_id=user.id, title="running", task_type="directed")
    DomainService(TaskRepository(db)).transition_task(
        user_id=user.id, task_id=task.id, command="submit", expected_version=1
    )
    # 让状态机直接置 RUNNING（等价 start）
    DomainService(TaskRepository(db)).transition_task(
        user_id=user.id, task_id=task.id, command="start", expected_version=2
    )
    return TaskRepository(db).get_owned(user.id, task.id)


def test_pause_transitions_running_to_pausing(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    result = svc.pause_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
    )
    assert result.state == "PAUSING"


def test_double_pause_same_key_is_idempotent(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    key = "k-pause-1"
    first = svc.pause_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
        idempotency_key=key,
    )
    second = svc.pause_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
        idempotency_key=key,
    )
    assert first.state == second.state == "PAUSING"
    assert second.version == first.version  # 未重复递增


def test_cancel_twice_same_key_one_effect(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    first = svc.cancel_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
        idempotency_key="k-cancel-1",
    )
    second = svc.cancel_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
        idempotency_key="k-cancel-1",
    )
    assert first.state == second.state == "CANCELLING"
    assert second.version == first.version


def test_command_enqueues_outbox(db, user, running_task) -> None:
    TaskCommandService(db).pause_task(
        user_id=user.id,
        task_id=running_task.id,
        expected_version=running_task.version,
        idempotency_key="k-pause-2",
    )
    rows = db.query(OutboxEvent).filter_by(aggregate_type="task").all()
    assert any(r.event_type == "task.pause" for r in rows)
