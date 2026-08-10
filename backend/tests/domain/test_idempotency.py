"""Stable fingerprints + idempotency record/replay/conflict."""

from __future__ import annotations

import pytest
from app.domain.errors import IdempotencyConflictError
from app.domain.idempotency import (
    IdempotencyService,
    idempotency_key_for_artifact,
    idempotency_key_for_node,
    stable_fingerprint,
)


def test_stable_fingerprint_is_deterministic() -> None:
    a = stable_fingerprint({"b": 1, "a": [2, 3]}, "x")
    b = stable_fingerprint({"a": [2, 3], "b": 1}, "x")  # key order differs
    assert a == b


def test_node_key_derived_from_semantics() -> None:
    k1 = idempotency_key_for_node(
        task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp1"
    )
    k2 = idempotency_key_for_node(
        task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp1"
    )
    k3 = idempotency_key_for_node(
        task_id=1, spec_version=1, node_type="fetch", input_fingerprint="fp2"
    )
    assert k1 == k2 and k1 != k3


def test_artifact_key_is_stable() -> None:
    a = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash1")
    b = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash1")
    c = idempotency_key_for_artifact("v1", "csv", {"status": "passed"}, "hash2")
    assert a == b and a != c


def test_same_key_same_payload_reuses(db, user) -> None:
    service = IdempotencyService()
    first = service.record(
        db,
        user_id=user.id,
        operation="task.create",
        client_key="k-1",
        payload={"title": "t"},
        result_ref=("task", 10),
    )
    second = service.record(
        db,
        user_id=user.id,
        operation="task.create",
        client_key="k-1",
        payload={"title": "t"},
        result_ref=("task", 10),
    )
    assert first == (False, 10)
    assert second == (True, 10)


def test_same_key_different_payload_conflicts(db, user) -> None:
    service = IdempotencyService()
    service.record(
        db,
        user_id=user.id,
        operation="task.create",
        client_key="k-2",
        payload={"title": "a"},
        result_ref=("task", 11),
    )
    with pytest.raises(IdempotencyConflictError):
        service.record(
            db,
            user_id=user.id,
            operation="task.create",
            client_key="k-2",
            payload={"title": "b"},
            result_ref=("task", 12),
        )


def test_db_unique_is_backstop(db, user) -> None:
    from app.domain.idempotency import api_operation_key
    from app.domain.repository import IdempotencyRepository
    from sqlalchemy.exc import IntegrityError

    repo = IdempotencyRepository(db)
    key = api_operation_key("task.create", "k-3")
    repo.create(
        user_id=user.id,
        operation="task.create",
        key=key,
        payload_fingerprint="f1",
        result_ref_type="task",
        result_ref_id=13,
    )
    db.commit()
    with pytest.raises(IntegrityError):
        repo.create(
            user_id=user.id,
            operation="task.create",
            key=key,
            payload_fingerprint="f2",
            result_ref_type="task",
            result_ref_id=14,
        )
        db.commit()
