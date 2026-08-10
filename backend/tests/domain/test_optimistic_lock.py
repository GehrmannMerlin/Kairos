"""Optimistic concurrency: stale version never silently overwrites."""

from __future__ import annotations

import pytest
from app.domain.errors import StaleVersionError
from app.domain.repository import NodeRunRepository, RunRepository, TaskRepository


def test_stale_task_update_conflicts(db, user, task) -> None:
    repo = TaskRepository(db)
    repo.update_state(task, "QUEUED", expected_version=1)  # first write wins
    db.commit()
    stale = repo.get_owned(user.id, task.id)  # now version 2
    with pytest.raises(StaleVersionError):
        repo.update_state(stale, "RUNNING", expected_version=1)


def test_two_actors_no_silent_overwrite(db, user, task) -> None:
    from app.domain.service import DomainService

    s1 = DomainService(TaskRepository(db))
    s1.transition_task(
        user_id=user.id,
        task_id=task.id,
        command="submit",
        expected_version=1,
        actor_type="user",
        actor_id=user.id,
    )
    # a second actor with a stale read (expected_version 1) is rejected
    with pytest.raises(StaleVersionError):
        s1.transition_task(
            user_id=user.id,
            task_id=task.id,
            command="start",
            expected_version=1,
            actor_type="user",
            actor_id=user.id,
        )
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.version == 2  # first write preserved


def test_node_stale_update_conflicts(db, user, task) -> None:
    from app.domain.service import DomainService

    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(
        user_id=user.id, run_id=run.id, task_id=task.id, node_type="fetch"
    )
    service = DomainService(TaskRepository(db), NodeRunRepository(db))
    service.transition_node(
        user_id=user.id,
        node_run_id=node.id,
        command="ready",
        expected_version=1,
        actor_type="user",
        actor_id=user.id,
    )
    with pytest.raises(StaleVersionError):
        service.transition_node(
            user_id=user.id,
            node_run_id=node.id,
            command="dispatch",
            expected_version=1,
            actor_type="user",
            actor_id=user.id,
        )
