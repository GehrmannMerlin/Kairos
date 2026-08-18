"""Plan execution activity lifecycle integration."""

from __future__ import annotations

from typing import Any

import app.activities.plan_execution as plan_execution
import pytest
from app.activities.execution_seam import ExecuteUnitInput, ExecuteUnitResult, ExecutionUnit
from app.auth.models import User
from app.domain.models import DomainEvent, NodeAttempt, NodeRun
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _case(monkeypatch, tmp_path, name: str) -> tuple[Any, int]:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'{name}.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(plan_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email=f"{name}@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title=name, task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        return factory, run.id
    finally:
        session.close()


def _input(run_id: int) -> ExecuteUnitInput:
    return ExecuteUnitInput(
        run_id=run_id,
        unit=ExecutionUnit(
            run_id=run_id,
            index=1,
            unit_type="fetch",
            node_id="fetch-1",
            node_type="fetch",
            input_fingerprint="f" * 64,
        ),
    )


@pytest.mark.asyncio
async def test_execute_safe_unit_persists_lifecycle_for_direct_activity_test(
    monkeypatch, tmp_path
) -> None:
    factory, run_id = _case(monkeypatch, tmp_path, "plan-execution")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(
            unit_index=1, committed_refs={"fetched": 2}, safe_message="fetch completed"
        )

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    result = await plan_execution.execute_safe_unit(_input(run_id))

    assert result.status == "OK"
    session = factory()
    try:
        assert session.query(NodeRun).count() == 1
        assert session.query(NodeAttempt).one().attempt == 1
        events = list(session.query(DomainEvent).order_by(DomainEvent.id))
        assert [event.event_type for event in events] == [
            "run.node_started",
            "run.node_completed",
        ]
        assert events[-1].payload["safe_message"] == "fetch completed"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_records_safe_failure_before_reraising(
    monkeypatch, tmp_path
) -> None:
    factory, run_id = _case(monkeypatch, tmp_path, "plan-failure")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        raise RuntimeError("Authorization: private credential")

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    with pytest.raises(RuntimeError, match="private credential"):
        await plan_execution.execute_safe_unit(_input(run_id))

    session = factory()
    try:
        events = list(session.query(DomainEvent).order_by(DomainEvent.id))
        assert [event.event_type for event in events] == ["run.node_started", "run.node_failed"]
        assert events[-1].payload["reason_code"] == "INTERNAL"
        assert "private credential" not in str(events[-1].payload)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_records_lookup_failure_before_reraising(
    monkeypatch, tmp_path
) -> None:
    factory, run_id = _case(monkeypatch, tmp_path, "plan-lookup-failure")

    def raise_lookup(_: str | None) -> Any:
        raise RuntimeError("lookup")

    monkeypatch.setattr(plan_execution, "get_node_executor", raise_lookup)
    with pytest.raises(RuntimeError, match="lookup"):
        await plan_execution.execute_safe_unit(_input(run_id))

    session = factory()
    try:
        events = list(session.query(DomainEvent).order_by(DomainEvent.id))
        assert [event.event_type for event in events] == ["run.node_started", "run.node_failed"]
        assert events[-1].payload["reason_code"] == "INTERNAL"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_preserves_executor_error_when_lifecycle_finish_fails(
    monkeypatch, tmp_path, caplog
) -> None:
    _, run_id = _case(monkeypatch, tmp_path, "plan-lifecycle-failure")
    original = RuntimeError("executor failure")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        raise original

    class FailingLifecycle:
        def __init__(self, _session) -> None:
            pass

        def start_attempt(self, **_kwargs) -> None:
            pass

        def finish_attempt(self, **_kwargs) -> None:
            raise RuntimeError("Bearer lifecycle-secret")

    from app.execution import lifecycle

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    monkeypatch.setattr(lifecycle, "ExecutionLifecycleRecorder", FailingLifecycle)
    with pytest.raises(RuntimeError) as raised:
        await plan_execution.execute_safe_unit(_input(run_id))
    assert raised.value is original
    assert "run_id=1" in caplog.text
    assert "lifecycle-secret" not in caplog.text


@pytest.mark.asyncio
async def test_execute_safe_unit_normalizes_credential_required_reason(
    monkeypatch, tmp_path
) -> None:
    factory, run_id = _case(monkeypatch, tmp_path, "plan-credential-required")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(unit_index=1, committed_refs={}, status="CREDENTIAL_REQUIRED")

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    await plan_execution.execute_safe_unit(_input(run_id))
    session = factory()
    try:
        event = session.query(DomainEvent).order_by(DomainEvent.id.desc()).first()
        assert event.event_type == "run.node_blocked"
        assert event.payload["reason_code"] == "CREDENTIAL_REQUIRED"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_finalizes_attempt_on_cancellation(monkeypatch, tmp_path) -> None:
    """M-11 P0：CancelledError 不能被 except Exception 吞掉，attempt 必须收口 CANCELLED。

    过去这里依赖 except Exception 兜底，但 Python ≥3.11 的 asyncio.CancelledError 继承
    BaseException，导致 finish_attempt 永不执行、NodeAttempt 残留 RUNNING。
    """
    import asyncio

    factory, run_id = _case(monkeypatch, tmp_path, "plan-cancel")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        raise asyncio.CancelledError()

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    with pytest.raises(asyncio.CancelledError):
        await plan_execution.execute_safe_unit(_input(run_id))

    session = factory()
    try:
        attempt = session.query(NodeAttempt).one()
        node = session.query(NodeRun).one()
        events = [e.event_type for e in session.query(DomainEvent).order_by(DomainEvent.id)]
        # 取消传播 + attempt 终态收口，不残留 RUNNING
        assert attempt.status == "CANCELLED"
        assert attempt.finished_at is not None
        assert node.state == "CANCELLED"
        assert node.finished_at is not None
        assert events == ["run.node_started", "run.node_cancelled"]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_more_pending_is_succeeded(monkeypatch, tmp_path) -> None:
    """M-11：小批次返回 MORE_PENDING 时 attempt 记为 SUCCEEDED（本批已提交）。"""
    factory, run_id = _case(monkeypatch, tmp_path, "plan-more-pending")

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(
            unit_index=1,
            status="MORE_PENDING",
            committed_refs={
                "extracted": 5,
                "failed": 0,
                "remaining": 3,
                "batch_identity": "extract-1-1-10",
            },
        )

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    result = await plan_execution.execute_safe_unit(_input(run_id))
    assert result.status == "MORE_PENDING"

    session = factory()
    try:
        attempt = session.query(NodeAttempt).one()
        events = [e.event_type for e in session.query(DomainEvent).order_by(DomainEvent.id)]
        assert attempt.status == "SUCCEEDED"
        assert events == ["run.node_started", "run.node_completed"]
    finally:
        session.close()
