"""Execution metrics expose only stable, low-cardinality dimensions."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import app.activities.task_execution as task_execution
import app.execution.lifecycle as lifecycle
import app.plan.preflight as preflight_module
import app.plan.service as plan_service_module
import pytest
from app.activities.execution_seam import ExecutionUnit
from app.activities.task_execution import CompleteRunInput, _eligible_count, complete_run
from app.auth.models import User
from app.domain.repository import RunRepository, TaskRepository
from app.execution.lifecycle import ExecutionLifecycleRecorder
from app.infra.db import Base
from app.observability.execution_metrics import ExecutionMetrics
from app.plan.service import PlanService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from tests.plan.test_execution_preflight import PreflightCase, _case


class _FakeInstrument:
    def __init__(self, calls: list[tuple[int, dict[str, str]]]) -> None:
        self._calls = calls

    def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
        self._calls.append((value, attributes or {}))


class _FakeMeter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str]]] = []

    def create_counter(self, _name: str, **_kwargs: Any) -> _FakeInstrument:
        return _FakeInstrument(self.calls)

    def create_up_down_counter(self, _name: str, **_kwargs: Any) -> _FakeInstrument:
        return _FakeInstrument(self.calls)

    def attribute_keys(self) -> set[str]:
        return {key for _, attributes in self.calls for key in attributes}


class _RecordingExecutionMetrics:
    def __init__(self) -> None:
        self.preflight: list[tuple[str, tuple[str, ...]]] = []
        self.nodes: list[tuple[str, str, str | None]] = []
        self.runs: list[tuple[str, str | None]] = []

    def record_preflight(self, *, status: str, issue_codes: Sequence[str]) -> None:
        self.preflight.append((status, tuple(issue_codes)))

    def record_node_terminal(self, *, node_type: str, state: str, reason_code: str | None) -> None:
        self.nodes.append((node_type, state, reason_code))

    def record_run_terminal(self, *, state: str, outcome_code: str | None) -> None:
        self.runs.append((state, outcome_code))

    def record_invariant_violation(self, *, invariant: str) -> None:
        pass


@pytest.fixture
def preflight_service_case(tmp_path) -> Iterator[PreflightCase]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metric-preflight.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield _case(session)
    finally:
        session.close()


def _patch_preflight_metrics(monkeypatch, metrics: _RecordingExecutionMetrics) -> None:
    monkeypatch.setattr(preflight_module, "get_execution_metrics", lambda: metrics, raising=False)
    monkeypatch.setattr(
        plan_service_module, "get_execution_metrics", lambda: metrics, raising=False
    )


def test_execution_metrics_use_only_stable_labels() -> None:
    fake_meter = _FakeMeter()
    metrics = ExecutionMetrics(fake_meter)

    metrics.record_preflight(
        status="BLOCKED",
        issue_codes=["SOURCE_RESOLUTION_REQUIRED", "FROZEN_CONFIG_UNAVAILABLE"],
    )
    metrics.record_node_terminal(
        node_type="fetch",
        state="FAILED",
        reason_code="NETWORK_TIMEOUT",
    )
    metrics.record_run_terminal(state="PARTIALLY_COMPLETED", outcome_code="RUNTIME_LIMIT")
    metrics.record_sse_replay(count=3)
    metrics.change_sse_connections(delta=1)
    metrics.change_sse_connections(delta=-1)
    metrics.record_invariant_violation(invariant="eligible_zero_partial")

    assert fake_meter.attribute_keys() <= {
        "status",
        "issue_code",
        "node_type",
        "state",
        "reason_code",
        "outcome_code",
        "invariant",
    }
    assert "task_id" not in fake_meter.attribute_keys()
    assert "user_id" not in fake_meter.attribute_keys()
    assert "url" not in fake_meter.attribute_keys()
    assert "exception" not in fake_meter.attribute_keys()


def test_execution_metrics_omit_absent_optional_labels() -> None:
    fake_meter = _FakeMeter()
    metrics = ExecutionMetrics(fake_meter)

    metrics.record_preflight(status="READY", issue_codes=[])
    metrics.record_node_terminal(node_type="validate", state="SUCCEEDED", reason_code=None)
    metrics.record_run_terminal(state="COMPLETED", outcome_code=None)

    assert all(value == 1 for value, _ in fake_meter.calls)
    assert all(None not in attributes.values() for _, attributes in fake_meter.calls)


def test_partial_invariant_reads_persisted_eligible_url_count() -> None:
    class _Session:
        def scalar(self, _statement: Any) -> Any:
            return SimpleNamespace(scope_completion_metadata={"eligible_urls": 0})

    inp = SimpleNamespace(user_id=1, task_id=2, run_id=3)

    assert _eligible_count(_Session(), inp) == 0


def test_preflight_metric_records_only_committed_winner(
    preflight_service_case: PreflightCase, monkeypatch
) -> None:
    metrics = _RecordingExecutionMetrics()
    _patch_preflight_metrics(monkeypatch, metrics)
    case = preflight_service_case
    service = PlanService(case.db, starter=None)
    kwargs: dict[str, Any] = {
        "user_id": case.user.id,
        "task_id": case.task.id,
        "spec_version": case.spec.version,
        "plan_version": case.plan.version,
        "settings": case.settings,
    }

    winner = service.require_ready_preflight(**kwargs)
    replay = service.require_ready_preflight(**kwargs)

    assert winner.created is True
    assert replay.created is False
    assert metrics.preflight == [(winner.status.value, tuple(winner.issue_codes))]


def test_preflight_metric_omits_rolled_back_commit(
    preflight_service_case: PreflightCase, monkeypatch
) -> None:
    metrics = _RecordingExecutionMetrics()
    _patch_preflight_metrics(monkeypatch, metrics)
    case = preflight_service_case
    service = PlanService(case.db, starter=None)

    def fail_commit() -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(case.db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        service.require_ready_preflight(
            user_id=case.user.id,
            task_id=case.task.id,
            spec_version=case.spec.version,
            plan_version=case.plan.version,
            settings=case.settings,
        )

    case.db.rollback()
    assert metrics.preflight == []


def test_preflight_metric_omits_nested_rollback_before_outer_commit(
    preflight_service_case: PreflightCase, monkeypatch
) -> None:
    metrics = _RecordingExecutionMetrics()
    _patch_preflight_metrics(monkeypatch, metrics)
    case = preflight_service_case
    nested = case.db.begin_nested()

    outcome = case.service.evaluate(
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        plan_version=case.plan.version,
    )
    assert outcome.created is True
    nested.rollback()
    case.db.commit()

    assert metrics.preflight == []


def test_preflight_metric_pending_state_does_not_survive_session_close(
    preflight_service_case: PreflightCase, monkeypatch
) -> None:
    metrics = _RecordingExecutionMetrics()
    _patch_preflight_metrics(monkeypatch, metrics)
    case = preflight_service_case

    outcome = case.service.evaluate(
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        plan_version=case.plan.version,
    )
    assert outcome.created is True
    case.db.close()
    case.db.commit()

    assert metrics.preflight == []


def test_node_terminal_metric_records_winner_not_idempotent_loser(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metric-node.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    metrics = _RecordingExecutionMetrics()
    monkeypatch.setattr(lifecycle, "get_execution_metrics", lambda: metrics)
    try:
        user = User(email="metric-node@example.com", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="node", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
        )
        unit = ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="fetch",
            input_fingerprint="f" * 64,
            node_id="fetch-1",
            node_type="fetch",
        )
        recorder = ExecutionLifecycleRecorder(session)
        recorder.start_attempt(run_id=run.id, unit=unit, attempt=1)

        for _ in range(2):
            recorder.finish_attempt(
                run_id=run.id,
                unit=unit,
                attempt=1,
                status="FAILED",
                committed_refs={},
                error_code="NETWORK",
            )

        assert metrics.nodes == [("fetch", "FAILED", "NETWORK")]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_run_terminal_metric_records_winner_not_idempotent_loser(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metric-run.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    metrics = _RecordingExecutionMetrics()
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    monkeypatch.setattr(task_execution, "get_execution_metrics", lambda: metrics)
    monkeypatch.setattr(task_execution, "_release_task_slot", lambda *_args, **_kwargs: None)
    session: Session = factory()
    try:
        user = User(email="metric-run@example.com", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="run", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
        )
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        command = CompleteRunInput(task_id=task.id, user_id=user.id, run_id=run.id)
    finally:
        session.close()

    await complete_run(command)
    await complete_run(command)

    assert metrics.runs == [("COMPLETED", "workflow_completed")]
