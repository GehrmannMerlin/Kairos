"""ORM roundtrip: create core objects and read them back."""

from __future__ import annotations

from app.domain.repository import (
    NodeRunRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)


def test_task_roundtrip(db, user) -> None:
    repo = TaskRepository(db)
    task = repo.create(user_id=user.id, title="t", task_type="directed")
    fetched = repo.get_owned(user.id, task.id)
    assert fetched.title == "t"
    assert fetched.state == "draft"
    assert fetched.version == 1


def test_run_and_spec_roundtrip(db, user, task) -> None:
    spec = SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="v1",
        payload={"fields": ["url", "title"]},
    )
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    node = NodeRunRepository(db).create(
        user_id=user.id,
        run_id=run.id,
        task_id=task.id,
        node_type="fetch",
        input_fingerprint="abc",
    )
    assert spec.version == 1
    assert run.state == "pending"
    assert node.state == "pending"
