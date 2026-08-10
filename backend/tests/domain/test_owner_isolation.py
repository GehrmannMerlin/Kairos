"""Owner isolation across core domain entities (M-04)."""

from __future__ import annotations

import pytest
from app.auth import errors as aerr
from app.auth.repository import UserRepository
from app.domain.repository import (
    NodeRunRepository,
    RunRepository,
    TaskRepository,
)


def test_b_cannot_read_a_task(db) -> None:
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=alice.id, title="t", task_type="directed")
    with pytest.raises(aerr.NotFoundError):
        TaskRepository(db).get_owned(bob.id, task.id)


def test_b_cannot_read_a_run_or_node(db) -> None:
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=alice.id, title="t", task_type="directed")
    run = RunRepository(db).create(
        user_id=alice.id, task_id=task.id, spec_version=1, plan_version=1
    )
    node = NodeRunRepository(db).create(
        user_id=alice.id, run_id=run.id, task_id=task.id, node_type="fetch"
    )
    with pytest.raises(aerr.NotFoundError):
        RunRepository(db).get_owned(bob.id, run.id)
    with pytest.raises(aerr.NotFoundError):
        NodeRunRepository(db).get_owned(bob.id, node.id)


def test_list_is_user_scoped(db) -> None:
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    TaskRepository(db).create(user_id=alice.id, title="a", task_type="directed")
    TaskRepository(db).create(user_id=alice.id, title="b", task_type="directed")
    bob_ids = {t.id for t in TaskRepository(db).list_by_user(bob.id)}
    assert bob_ids == set()


def test_b_cannot_transition_a_task(db) -> None:
    alice = UserRepository(db).create("alice@example.com", "hash", None)
    bob = UserRepository(db).create("bob@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=alice.id, title="t", task_type="directed")
    from app.domain.service import DomainService

    with pytest.raises(aerr.NotFoundError):
        DomainService(TaskRepository(db)).transition_task(
            user_id=bob.id,
            task_id=task.id,
            command="submit",
            expected_version=1,
            actor_type="user",
            actor_id=bob.id,
        )
