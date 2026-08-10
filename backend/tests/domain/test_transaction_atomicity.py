"""State+event+outbox commit in one transaction; a mid-failure rolls back all."""

from __future__ import annotations

import pytest
from app.domain.models import DomainEvent, NodeAttempt, OutboxEvent
from app.domain.repository import (
    NodeRunRepository,
    RunRepository,
    TaskRepository,
)
from app.state.states import NodeState, TaskState


def test_transition_writes_state_event_outbox(db, service, user, task) -> None:
    service.transition_task(
        user_id=user.id,
        task_id=task.id,
        command="submit",
        expected_version=1,
        actor_type="user",
        actor_id=user.id,
        reason="spec confirmed",
    )
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.QUEUED.value
    assert fresh.version == 2
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() == 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 1


def test_mid_transaction_failure_rolls_back_everything(
    db, service, user, task, monkeypatch
) -> None:
    def _boom(db, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("outbox down")

    monkeypatch.setattr("app.domain.service.enqueue_outbox", _boom)
    with pytest.raises(RuntimeError):
        service.transition_task(
            user_id=user.id,
            task_id=task.id,
            command="submit",
            expected_version=1,
            actor_type="user",
            actor_id=user.id,
            reason="boom",
        )
    db.rollback()
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value  # state not changed
    assert fresh.version == 1
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() == 0
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 0


def test_node_transition_and_attempt(db, service, user, task) -> None:
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(
        user_id=user.id, run_id=run.id, task_id=task.id, node_type="fetch"
    )
    service.transition_node(
        user_id=user.id,
        node_run_id=node.id,
        command="ready",
        expected_version=1,
        actor_type="user",
        actor_id=user.id,
    )
    service.transition_node(
        user_id=user.id,
        node_run_id=node.id,
        command="dispatch",
        expected_version=2,
        actor_type="user",
        actor_id=user.id,
    )
    db.expire_all()
    fresh = NodeRunRepository(db).get_owned(user.id, node.id)
    assert fresh.state == NodeState.RUNNING.value
    assert fresh.version == 3
    # entering RUNNING creates attempt #1
    assert db.query(NodeAttempt).filter(NodeAttempt.node_run_id == node.id).count() == 1
