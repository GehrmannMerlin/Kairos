"""M-04 Domain Smoke: create -> spec -> plan -> run -> node -> transitions ->
checkpoint -> replay -> conflict -> owner block -> failed txn rollback."""

from __future__ import annotations

import pytest
from app.auth import errors as aerr
from app.auth.repository import UserRepository
from app.domain.errors import IdempotencyConflictError, StaleVersionError
from app.domain.idempotency import IdempotencyService
from app.domain.models import (
    Checkpoint,
    DomainEvent,
    OutboxEvent,
    Record,
)
from app.domain.repository import (
    NodeRunRepository,
    PlanVersionRepository,
    RecordRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.domain.service import DomainService
from app.state.states import TaskState


@pytest.fixture()
def smoke(db):
    users = UserRepository(db)
    alice = users.create("alice@example.com", "hash", None)
    bob = users.create("bob@example.com", "hash", None)
    return {
        "db": db,
        "alice": alice,
        "bob": bob,
        "tasks": TaskRepository(db),
        "runs": RunRepository(db),
        "nodes": NodeRunRepository(db),
        "specs": SpecVersionRepository(db),
        "plans": PlanVersionRepository(db),
        "records": RecordRepository(db),
        "service": DomainService(TaskRepository(db)),
    }


def test_domain_smoke(smoke) -> None:
    db, alice, bob = smoke["db"], smoke["alice"], smoke["bob"]

    # 1. Create task + spec v1 + plan v1 + run + node
    task = smoke["tasks"].create(user_id=alice.id, title="采集", task_type="directed")
    smoke["specs"].create(
        user_id=alice.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="v1",
        payload={"fields": ["url"]},
    )
    smoke["plans"].create(
        user_id=alice.id, task_id=task.id, spec_version=1, version=1, payload={"nodes": ["fetch"]}
    )
    run = smoke["runs"].create(user_id=alice.id, task_id=task.id, spec_version=1, plan_version=1)
    node = smoke["nodes"].create(
        user_id=alice.id,
        run_id=run.id,
        task_id=task.id,
        node_type="fetch",
        input_fingerprint="fp-1",
    )

    # 2. Legal transitions: task submit -> queued; node ready -> running -> succeeded
    smoke["service"].transition_task(
        user_id=alice.id,
        task_id=task.id,
        command="submit",
        expected_version=1,
        actor_type="user",
        actor_id=alice.id,
    )
    smoke["service"].transition_node(
        user_id=alice.id,
        node_run_id=node.id,
        command="ready",
        expected_version=1,
        actor_type="system",
    )
    smoke["service"].transition_node(
        user_id=alice.id,
        node_run_id=node.id,
        command="dispatch",
        expected_version=2,
        actor_type="system",
    )
    smoke["service"].transition_node(
        user_id=alice.id,
        node_run_id=node.id,
        command="succeed",
        expected_version=3,
        actor_type="system",
    )

    # 3. Write a record + checkpoint AFTER the committed batch
    rec = smoke["records"].create(
        user_id=alice.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        payload={"url": "https://a.example"},
    )
    cp = smoke["service"].commit_checkpoint(
        user_id=alice.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity="batch-1",
        spec_version=1,
        plan_version=1,
        node_run_id=node.id,
        input_fingerprint="fp-1",
        committed_refs={"records": [rec.id]},
        content_hash="h1",
    )
    assert cp.batch_identity == "batch-1"
    assert db.query(Checkpoint).filter(Checkpoint.run_id == run.id).count() == 1

    # 4. Replay same batch -> reuse, no duplicate record
    smoke["service"].commit_checkpoint(
        user_id=alice.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity="batch-1",
        spec_version=1,
        plan_version=1,
        node_run_id=node.id,
        input_fingerprint="fp-1",
        committed_refs={"records": [rec.id]},
        content_hash="h1",
    )
    assert db.query(Record).filter(Record.task_id == task.id).count() == 1

    # 5. Stale version -> CONFLICT
    with pytest.raises(StaleVersionError):
        smoke["service"].transition_task(
            user_id=alice.id,
            task_id=task.id,
            command="start",
            expected_version=1,
            actor_type="user",
            actor_id=alice.id,
        )

    # 6. User B cannot read A's task
    with pytest.raises(aerr.NotFoundError):
        smoke["tasks"].get_owned(bob.id, task.id)

    # 7. Idempotency: same key+payload reuse; different payload conflict
    idem = IdempotencyService()
    replay, ref = idem.record(
        db,
        user_id=alice.id,
        operation="task.create",
        client_key="smoke-1",
        payload={"title": "t"},
        result_ref=("task", task.id),
    )
    assert replay is False
    replay2, ref2 = idem.record(
        db,
        user_id=alice.id,
        operation="task.create",
        client_key="smoke-1",
        payload={"title": "t"},
        result_ref=("task", task.id),
    )
    assert replay2 is True and ref2 == task.id
    with pytest.raises(IdempotencyConflictError):
        idem.record(
            db,
            user_id=alice.id,
            operation="task.create",
            client_key="smoke-1",
            payload={"title": "DIFFERENT"},
            result_ref=("task", 999),
        )

    # 8. Failed batch -> rollback, no checkpoint, no half state
    with pytest.raises(StaleVersionError):
        smoke["service"].transition_task(
            user_id=alice.id,
            task_id=task.id,
            command="start",
            expected_version=1,
            actor_type="user",
            actor_id=alice.id,
        )
    db.rollback()
    fresh = smoke["tasks"].get_owned(alice.id, task.id)
    assert fresh.state == TaskState.QUEUED.value  # unchanged
    assert db.query(Checkpoint).filter(Checkpoint.batch_identity == "batch-2").count() == 0

    # 9. events + outbox recorded
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() >= 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() >= 1
