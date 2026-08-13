"""CollectionSpec confirm / immutable version / transaction atomicity (TEST D + F)."""

from __future__ import annotations

import pytest
from app.domain.errors import StaleVersionError
from app.domain.models import DomainEvent, OutboxEvent
from app.domain.repository import SpecDraftRepository, SpecVersionRepository, TaskRepository
from app.state.states import TaskState


def _payload(goal: str = "搜集供应商", field: str = "公司名") -> dict:
    return {
        "schema_version": "m06.1",
        "task_type": "EXPLORATORY",
        "goal": goal,
        "fields": [{"name": field, "type": "text", "required": True}],
        "auto_expand_fields": True,
        "source_scope": {"mode": "EXPLORATORY", "seed_urls": [], "source_hints": []},
        "completion_conditions": [{"kind": "min_records", "target": 20}],
        "advanced_settings": {},
        "field_expansion": {},
    }


def test_saving_draft_does_not_create_version(db, service, user, task) -> None:
    SpecDraftRepository(db).upsert(user_id=user.id, task_id=task.id, payload=_payload())
    db.expire_all()
    assert SpecVersionRepository(db).latest_version(user.id, task.id) is None
    assert TaskRepository(db).get_owned(user.id, task.id).current_spec_version is None


def test_confirm_freezes_v1_and_transitions(db, service, user, task) -> None:
    v1 = service.confirm_spec(
        user_id=user.id,
        task_id=task.id,
        expected_version=1,
        spec_payload=_payload(),
        actor_id=user.id,
    )
    assert v1.version == 1
    assert v1.confirmed_at is not None

    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.QUEUED.value
    assert fresh.current_spec_version == 1
    assert fresh.task_type == "EXPLORATORY"
    assert (
        db.query(DomainEvent)
        .filter(
            DomainEvent.aggregate_id == task.id,
            DomainEvent.event_type == "task.spec_confirmed",
        )
        .count()
        == 1
    )
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 1


def test_revision_creates_immutable_v2(db, service, user, task) -> None:
    v1 = service.confirm_spec(
        user_id=user.id,
        task_id=task.id,
        expected_version=1,
        spec_payload=_payload(goal="v1"),
        actor_id=user.id,
    )
    # task is QUEUED now (version 2); revising before execution makes v2.
    v2 = service.confirm_spec(
        user_id=user.id,
        task_id=task.id,
        expected_version=2,
        spec_payload=_payload(goal="v2"),
        actor_id=user.id,
    )
    assert v1.version == 1 and v2.version == 2
    assert v1.payload["goal"] == "v1"
    assert v2.payload["goal"] == "v2"

    reloaded_v1 = SpecVersionRepository(db).get_version(user.id, task.id, 1)
    assert reloaded_v1.payload["goal"] == "v1"
    assert reloaded_v1.payload == v1.payload  # v1 was never overwritten


def test_stale_version_conflict_creates_nothing(db, service, user, task) -> None:
    with pytest.raises(StaleVersionError):
        service.confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=99,
            spec_payload=_payload(),
            actor_id=user.id,
        )
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value
    assert fresh.current_spec_version is None
    assert SpecVersionRepository(db).latest_version(user.id, task.id) is None


def test_confirm_mid_transaction_rolls_back_everything(
    db, service, user, task, monkeypatch
) -> None:
    def _boom(db, **kwargs):  # noqa: ANN001, ANN002
        raise RuntimeError("outbox down")

    monkeypatch.setattr("app.domain.service.enqueue_outbox", _boom)
    with pytest.raises(RuntimeError):
        service.confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=1,
            spec_payload=_payload(),
            actor_id=user.id,
        )
    db.rollback()
    db.expire_all()
    fresh = TaskRepository(db).get_owned(user.id, task.id)
    assert fresh.state == TaskState.DRAFT.value
    assert fresh.version == 1
    assert fresh.current_spec_version is None
    assert SpecVersionRepository(db).latest_version(user.id, task.id) is None
    assert db.query(DomainEvent).filter(DomainEvent.aggregate_id == task.id).count() == 0
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == task.id).count() == 0


def test_confirm_rejects_running_state(db, service, user, task) -> None:
    service.transition_task(
        user_id=user.id,
        task_id=task.id,
        command="submit",
        expected_version=1,
        actor_type="user",
        actor_id=user.id,
    )
    service.transition_task(
        user_id=user.id,
        task_id=task.id,
        command="start",
        expected_version=2,
        actor_type="system",
        actor_id=None,
    )
    from app.domain.errors import IllegalTransitionError

    with pytest.raises(IllegalTransitionError):
        service.confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=3,
            spec_payload=_payload(),
            actor_id=user.id,
        )
