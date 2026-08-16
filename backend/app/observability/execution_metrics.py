"""Low-cardinality OpenTelemetry metrics for canonical execution facts."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any


class ExecutionMetrics:
    """Record execution signals without user- or task-specific dimensions."""

    def __init__(self, meter: Any) -> None:
        self._preflight = meter.create_counter("kairos.execution.preflight")
        self._preflight_issues = meter.create_counter("kairos.execution.preflight.issues")
        self._node_terminal = meter.create_counter("kairos.execution.node.terminal")
        self._run_terminal = meter.create_counter("kairos.execution.run.terminal")
        self._sse_replay = meter.create_counter("kairos.execution.sse.replayed_events")
        self._sse_connections = meter.create_up_down_counter(
            "kairos.execution.sse.active_connections"
        )
        self._invariant_violations = meter.create_counter("kairos.execution.invariant_violations")

    def record_preflight(self, *, status: str, issue_codes: Sequence[str]) -> None:
        self._preflight.add(1, attributes={"status": status})
        for issue_code in sorted(set(issue_codes)):
            self._preflight_issues.add(
                1,
                attributes={"status": status, "issue_code": issue_code},
            )

    def record_node_terminal(
        self,
        *,
        node_type: str,
        state: str,
        reason_code: str | None,
    ) -> None:
        attributes = {"node_type": node_type, "state": state}
        if reason_code is not None:
            attributes["reason_code"] = reason_code
        self._node_terminal.add(1, attributes=attributes)

    def record_run_terminal(self, *, state: str, outcome_code: str | None) -> None:
        attributes = {"state": state}
        if outcome_code is not None:
            attributes["outcome_code"] = outcome_code
        self._run_terminal.add(1, attributes=attributes)

    def record_sse_replay(self, *, count: int) -> None:
        self._sse_replay.add(count)

    def change_sse_connections(self, *, delta: int) -> None:
        self._sse_connections.add(delta)

    def record_invariant_violation(self, *, invariant: str) -> None:
        self._invariant_violations.add(1, attributes={"invariant": invariant})


@lru_cache(maxsize=1)
def get_execution_metrics() -> ExecutionMetrics:
    from opentelemetry import metrics

    return ExecutionMetrics(metrics.get_meter("kairos.execution"))
