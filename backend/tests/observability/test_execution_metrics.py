"""Execution metrics expose only stable, low-cardinality dimensions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.activities.task_execution import _eligible_count
from app.observability.execution_metrics import ExecutionMetrics


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
