"""Terminal reconciliation service tests (P0-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from app.reconciliation.service import (
    query_stale_runs,
    reconcile_stale_runs,
    resolve_terminal_command,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_EMAIL = count(1)


def test_resolve_terminal_command_cancelled():
    assert resolve_terminal_command(temporal_status="CANCELED", is_partial=None) == "mark_cancelled"


def test_resolve_terminal_command_completed_uses_persisted_decision():
    assert resolve_terminal_command(temporal_status="COMPLETED", is_partial=True) == "mark_partial"
    assert resolve_terminal_command(temporal_status="COMPLETED", is_partial=False) == "complete"


def test_resolve_terminal_command_completed_without_decision_fails_closed():
    # workflow COMPLETED 但从未持久化 CompletionDecision → 不得猜数据结果
    assert resolve_terminal_command(temporal_status="COMPLETED", is_partial=None) == "fail"


def test_resolve_terminal_command_any_other_terminal_fails():
    for status in ("FAILED", "TERMINATED", "TIMED_OUT", None, "RUNNING"):
        assert resolve_terminal_command(temporal_status=status, is_partial=False) == "fail"


def _engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _make_factory():
    from app.infra.db import Base

    engine = _engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def factory():
    return _make_factory()


def _seed_run(factory, *, state="running", age_seconds=7200, with_decision=None):
    from app.auth.repository import UserRepository
    from app.domain.repository import RunRepository, TaskRepository

    db = factory()
    try:
        user = UserRepository(db).create(f"reconcile{next(_EMAIL)}@example.com", "hash", None)
        task = TaskRepository(db).create(
            user_id=user.id, title="reconcile", task_type="SPECIFIED_SOURCE"
        )
        run = RunRepository(db).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        run.state = state
        run.started_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        db.add(run)
        if with_decision is not None:
            from app.validation.repository import ValidationRepository

            ValidationRepository(db).create_completion(
                user_id=user.id,
                task_id=task.id,
                run_id=run.id,
                spec_version=1,
                plan_version=1,
                decision={"is_partial": with_decision, "status": "PARTIALLY_COMPLETED"},
            )
        db.commit()
        return {"db": db, "user": user, "task": task, "run": run}
    finally:
        db.close()


def test_query_stale_runs_only_running_and_stale(factory):

    seeded = _seed_run(factory, age_seconds=7200)  # running + stale
    _seed_run(factory, age_seconds=60)  # running + fresh → excluded
    # completed run (old) → excluded by state
    done = _seed_run(factory, state="completed", age_seconds=7200)

    db = factory()
    try:
        stale = query_stale_runs(db, stale_after_seconds=3600)
    finally:
        db.close()
    ids = {r.run_id for r in stale}
    assert seeded["run"].id in ids
    assert done["run"].id not in ids
    assert len(stale) == 1  # fresh running run excluded by staleness


@pytest.mark.asyncio
async def test_reconcile_dry_run_skips_active_workflow(factory):
    seeded = _seed_run(factory, age_seconds=7200)

    async def status_fn(workflow_id: str) -> str | None:
        return "RUNNING"  # workflow still alive → must skip

    results = await reconcile_stale_runs(
        workflow_status_fn=status_fn,
        stale_after_seconds=3600,
        dry_run=True,
        session_factory=factory,
    )
    assert results[0]["run_id"] == seeded["run"].id
    assert results[0]["action"] == "skip"


@pytest.mark.asyncio
async def test_reconcile_dry_run_reports_without_applying(factory):
    seeded = _seed_run(factory, age_seconds=7200, with_decision=False)
    applied: list[tuple[str, int]] = []

    async def status_fn(workflow_id: str) -> str | None:
        return "COMPLETED"

    async def apply_fn(command: str, run) -> None:
        applied.append((command, run.run_id))

    results = await reconcile_stale_runs(
        workflow_status_fn=status_fn,
        stale_after_seconds=3600,
        dry_run=True,
        session_factory=factory,
        apply_fn=apply_fn,
    )
    assert results[0]["run_id"] == seeded["run"].id
    assert results[0]["action"] == "complete"
    assert results[0]["applied"] is False
    assert applied == []  # dry-run 绝不写


@pytest.mark.asyncio
async def test_reconcile_apply_dispatches_terminal_command(factory):
    seeded = _seed_run(factory, age_seconds=7200, with_decision=True)
    applied: list[tuple[str, int]] = []

    async def status_fn(workflow_id: str) -> str | None:
        return "COMPLETED"

    async def apply_fn(command: str, run) -> None:
        applied.append((command, run.run_id))

    results = await reconcile_stale_runs(
        workflow_status_fn=status_fn,
        stale_after_seconds=3600,
        dry_run=False,
        session_factory=factory,
        apply_fn=apply_fn,
    )
    assert applied == [("mark_partial", seeded["run"].id)]
    assert results[0]["applied"] is True


@pytest.mark.asyncio
async def test_reconcile_apply_lost_workflow_fails(factory):
    seeded = _seed_run(factory, age_seconds=7200)
    applied: list[tuple[str, int]] = []

    async def status_fn(workflow_id: str) -> str | None:
        return None  # workflow not found in Temporal → lost

    async def apply_fn(command: str, run) -> None:
        applied.append((command, run.run_id))

    await reconcile_stale_runs(
        workflow_status_fn=status_fn,
        stale_after_seconds=3600,
        dry_run=False,
        session_factory=factory,
        apply_fn=apply_fn,
    )
    assert applied == [("fail", seeded["run"].id)]


@pytest.mark.asyncio
async def test_reconcile_default_factory_obtains_session(factory, monkeypatch):
    # 生产默认 get_session_factory() 返回 sessionmaker，服务必须调用两次
    # (get_session_factory()()) 才能拿到 Session；注入 sessionmaker 复现该契约，
    # 若服务只调用一次就会拿到 sessionmaker 而报 AttributeError。
    seeded = _seed_run(factory, age_seconds=7200)

    import app.reconciliation.service as svc

    monkeypatch.setattr(svc, "get_session_factory", lambda: factory)

    async def status_fn(workflow_id: str) -> str | None:
        return "RUNNING"

    results = await svc.reconcile_stale_runs(
        workflow_status_fn=status_fn, stale_after_seconds=3600, dry_run=True
    )
    assert results[0]["run_id"] == seeded["run"].id
    assert results[0]["action"] == "skip"
