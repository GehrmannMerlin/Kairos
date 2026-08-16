"""Checkpoint only after committed work; replay reuses; failed txn yields none."""

from __future__ import annotations

import pytest
from app.auth.errors import NotFoundError
from app.auth.models import User
from app.domain.errors import DomainError, StaleVersionError
from app.domain.models import Checkpoint
from app.domain.repository import RunRepository, TaskRepository
from app.state.states import TaskState


@pytest.fixture()
def run(db, user, task):
    return RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )


def _commit(service, user, task, run, *, batch="b1", fp="fp1", fail=False, transition=True):
    if fail:
        with pytest.raises(StaleVersionError):
            service.transition_task(
                user_id=user.id,
                task_id=task.id,
                command="submit",
                expected_version=999,
                actor_type="user",
                actor_id=user.id,
            )
        db = service._tasks._db
        db.rollback()
        return None
    if transition:
        service.transition_task(
            user_id=user.id,
            task_id=task.id,
            command="submit",
            expected_version=1,
            actor_type="user",
            actor_id=user.id,
        )
    db = service._tasks._db
    return service.commit_checkpoint(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity=batch,
        spec_version=1,
        plan_version=1,
        node_run_id=None,
        input_fingerprint=fp,
        committed_refs={"records": [1, 2]},
        content_hash="h1",
    )


def test_commit_checkpoint_after_committed_batch(db, service, user, task, run) -> None:
    cp = _commit(service, user, task, run)
    assert cp.batch_identity == "b1"
    assert db.query(Checkpoint).count() == 1


def test_replay_reuses_checkpoint(db, service, user, task, run) -> None:
    _commit(service, user, task, run)
    # replay of the same committed batch (no re-transition) reuses the checkpoint
    cp2 = _commit(service, user, task, run, batch="b1", transition=False)
    assert db.query(Checkpoint).count() == 1  # no duplicate
    assert cp2.batch_identity == "b1"


def test_same_batch_different_fingerprint_conflicts(db, service, user, task, run) -> None:
    _commit(service, user, task, run)
    with pytest.raises(DomainError):
        _commit(service, user, task, run, batch="b1", fp="fp-DIFFERENT")


def test_failed_transaction_produces_no_checkpoint(db, service, user, task, run) -> None:
    _commit(service, user, task, run, fail=True)
    assert db.query(Checkpoint).count() == 0
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value


def test_commit_checkpoint_reuses_same_batch(db, user, task) -> None:
    from app.domain.models import Checkpoint
    from app.domain.repository import TaskRepository
    from app.domain.service import DomainService

    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=0)
    svc = DomainService(TaskRepository(db))
    first = svc.commit_checkpoint(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity="unit-1",
        spec_version=1,
        plan_version=0,
        node_run_id=None,
        input_fingerprint="fp-1",
        committed_refs={"n": 1},
        content_hash=None,
    )
    second = svc.commit_checkpoint(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity="unit-1",
        spec_version=1,
        plan_version=0,
        node_run_id=None,
        input_fingerprint="fp-1",
        committed_refs={"n": 1},
        content_hash=None,
    )
    assert isinstance(first, Checkpoint)
    assert isinstance(second, Checkpoint)
    assert second.id == first.id  # 复用，不重复提交

    rows = db.query(Checkpoint).filter_by(run_id=run.id).all()
    assert len(rows) == 1


def test_checkpoint_rejects_cross_owner_run_and_version_mismatch(
    db, service, user, task, run
) -> None:
    other = User(email="checkpoint-other@kairos.test", password_hash="hash")
    db.add(other)
    db.commit()
    other_task = TaskRepository(db).create(user_id=other.id, title="other", task_type=None)
    other_run = RunRepository(db).create(
        user_id=other.id, task_id=other_task.id, spec_version=1, plan_version=1
    )
    with pytest.raises(NotFoundError):
        service.commit_checkpoint(
            user_id=user.id,
            task_id=task.id,
            run_id=other_run.id,
            batch_identity="cross-owner",
            spec_version=1,
            plan_version=1,
            node_run_id=None,
            input_fingerprint="fp",
            committed_refs={},
            content_hash=None,
        )
    with pytest.raises(DomainError):
        service.commit_checkpoint(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            batch_identity="version-mismatch",
            spec_version=2,
            plan_version=1,
            node_run_id=None,
            input_fingerprint="fp",
            committed_refs={},
            content_hash=None,
        )


def test_checkpoint_replay_rejects_mismatched_stored_identity(db, service, user, task, run) -> None:
    checkpoint = service.commit_checkpoint(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        batch_identity="stored-identity",
        spec_version=1,
        plan_version=1,
        node_run_id=None,
        input_fingerprint="fp",
        committed_refs={},
        content_hash=None,
    )
    checkpoint.plan_version = 99
    db.commit()
    with pytest.raises(DomainError, match="身份不匹配"):
        service.commit_checkpoint(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            batch_identity="stored-identity",
            spec_version=1,
            plan_version=1,
            node_run_id=None,
            input_fingerprint="fp",
            committed_refs={},
            content_hash=None,
        )
