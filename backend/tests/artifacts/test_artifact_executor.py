"""Task 5 contracts for the real GenerateArtifact executor."""

from __future__ import annotations

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.domain.models import Artifact, Record, Run
from app.domain.repository import TaskRepository
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def _unit(run: Run, *, parameters: dict | None = None) -> ExecutionUnit:
    return ExecutionUnit(
        run_id=run.id,
        index=7,
        unit_type="node",
        input_fingerprint="artifact-export-test",
        node_id="generate-1",
        node_type="generate_artifact",
        parameters=parameters,
    )


def _run(db, *, user_id: int, task_id: int) -> Run:
    run = Run(user_id=user_id, task_id=task_id, spec_version=1, plan_version=1)
    db.add(run)
    db.commit()
    return run


@pytest.mark.asyncio
async def test_generate_artifact_uses_run_owner_and_reuses_idempotent_export(
    db, user_a, task_a, storage, monkeypatch
) -> None:
    """Would fail if execution trusts node owner parameters or creates a second export."""
    from app.artifacts.executor import generate_artifact

    db.add(
        Record(
            user_id=user_a.id,
            task_id=task_a.id,
            spec_version=1,
            partition="passed",
            payload={"名称": "唯一记录"},
        )
    )
    db.commit()
    run = _run(db, user_id=user_a.id, task_id=task_a.id)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.artifacts.executor.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.artifacts.executor.get_object_storage", lambda: storage)

    unit = _unit(run, parameters={"user_id": 999999, "download_url": "must-not-leak"})
    first = await generate_artifact(unit)
    second = await generate_artifact(unit)

    assert first.status == second.status == "OK"
    assert first.committed_refs["artifact_id"] == second.committed_refs["artifact_id"]
    assert first.committed_refs["row_count"] == 1
    assert first.committed_refs["task_id"] == task_a.id
    assert "download_url" not in first.committed_refs
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_generate_artifact_binds_output_to_persisted_run_owner(
    db, user_b, storage, monkeypatch
) -> None:
    """Would fail if the executor accepts an owner from a node parameter."""
    from app.artifacts.executor import generate_artifact

    other_task = TaskRepository(db).create(user_id=user_b.id, title="other", task_type="directed")
    run = _run(db, user_id=user_b.id, task_id=other_task.id)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.artifacts.executor.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.artifacts.executor.get_object_storage", lambda: storage)

    result = await generate_artifact(_unit(run, parameters={"user_id": 1}))
    artifact = db.scalar(
        select(Artifact).where(Artifact.id == result.committed_refs["artifact_id"])
    )

    assert result.status == "OK"
    assert result.committed_refs["row_count"] == 0  # headers-only CSV is a valid export
    assert artifact is not None
    assert artifact.user_id == user_b.id
    assert artifact.task_id == other_task.id


@pytest.mark.asyncio
async def test_generate_artifact_returns_typed_storage_error(
    db, user_a, task_a, monkeypatch
) -> None:
    """Would fail if an object-storage exception is reported as export success."""
    from app.artifacts.executor import generate_artifact

    class BrokenStorage:
        async def exists(self, key: str) -> bool:
            raise OSError("storage unreachable")

    run = _run(db, user_id=user_a.id, task_id=task_a.id)
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.artifacts.executor.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.artifacts.executor.get_object_storage", BrokenStorage)

    result = await generate_artifact(_unit(run))

    assert result.status == "FAILED"
    assert result.error_code == "STORAGE_ERROR"
    assert result.committed_refs == {}
