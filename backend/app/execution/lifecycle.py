"""Persist safe, idempotent node execution lifecycle facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.activities.execution_seam import ExecutionUnit
from app.domain.models import Checkpoint, NodeAttempt, NodeRun, Run
from app.domain.repository import NodeAttemptRepository, NodeRunRepository, RunRepository
from app.state.events import append_domain_event

_TERMINAL_EVENT_TYPES = {
    "SUCCEEDED": "run.node_completed",
    "FAILED": "run.node_failed",
    "NODE_EXECUTOR_UNAVAILABLE": "run.node_failed",
    "BLOCKED": "run.node_blocked",
    "WAITING_APPROVAL": "run.node_blocked",
    "RESOURCE_WAITING": "run.node_blocked",
}
_NODE_STATES = {
    "SUCCEEDED": "SUCCEEDED",
    "FAILED": "FAILED",
    "NODE_EXECUTOR_UNAVAILABLE": "FAILED",
    "BLOCKED": "BLOCKED",
    "WAITING_APPROVAL": "BLOCKED",
    "RESOURCE_WAITING": "WAITING_RESOURCE",
}


@dataclass(frozen=True)
class LifecycleAttempt:
    node_run_id: int
    node_attempt_id: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _counts(committed_refs: dict[str, Any]) -> dict[str, int | float]:
    return {
        key: value
        for key, value in committed_refs.items()
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


class ExecutionLifecycleRecorder:
    """Writes a single lifecycle command and its event in one transaction."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._nodes = NodeRunRepository(db)
        self._attempts = NodeAttemptRepository(db)
        self._runs = RunRepository(db)

    def start_attempt(self, *, run_id: int, unit: ExecutionUnit, attempt: int) -> LifecycleAttempt:
        run, node, node_attempt, was_new = self._resolve_attempt(run_id, unit, attempt)
        if was_new:
            now = _utcnow()
            node.state = "RUNNING"
            node.started_at = node.started_at or now
            node.version += 1
            node_attempt.status = "RUNNING"
            node_attempt.started_at = now
            self._append(
                run=run,
                node=node,
                attempt=node_attempt,
                event_type="run.node_started",
                state="RUNNING",
                counts={},
                reason_code=None,
                safe_message=None,
            )
            self._db.commit()
        return LifecycleAttempt(node_run_id=node.id, node_attempt_id=node_attempt.id)

    def finish_attempt(
        self,
        *,
        run_id: int,
        unit: ExecutionUnit,
        attempt: int,
        status: str,
        committed_refs: dict[str, Any],
        error_code: str | None,
        safe_message: str | None = None,
    ) -> LifecycleAttempt:
        run, node, node_attempt, _ = self._resolve_attempt(run_id, unit, attempt)
        if node_attempt.finished_at is not None and node_attempt.status == status:
            return LifecycleAttempt(node_run_id=node.id, node_attempt_id=node_attempt.id)

        now = _utcnow()
        event_type = _TERMINAL_EVENT_TYPES.get(status, "run.node_progress")
        node_state = _NODE_STATES.get(status, "RUNNING")
        node_attempt.status = status
        node_attempt.error_code = error_code
        node_attempt.error_summary = (safe_message or "")[:500] or None
        node_attempt.started_at = node_attempt.started_at or now
        if event_type != "run.node_progress":
            node_attempt.finished_at = now
            node.finished_at = now
        node.state = node_state
        node.version += 1
        self._append(
            run=run,
            node=node,
            attempt=node_attempt,
            event_type=event_type,
            state=node_state,
            counts=_counts(committed_refs),
            reason_code=error_code,
            safe_message=safe_message,
        )
        self._db.commit()
        return LifecycleAttempt(node_run_id=node.id, node_attempt_id=node_attempt.id)

    def checkpoint_committed(self, checkpoint: Checkpoint) -> None:
        run = self._runs.get_owned(checkpoint.user_id, checkpoint.run_id)
        node = (
            self._nodes.get_owned(checkpoint.user_id, checkpoint.node_run_id)
            if checkpoint.node_run_id is not None
            else None
        )
        payload = {
            "schema_version": 1,
            "task_id": run.task_id,
            "run_id": run.id,
            "plan_version": run.plan_version,
            "node_id": node.node_id if node is not None else None,
            "node_type": node.node_type if node is not None else None,
            "attempt": None,
            "state": "COMMITTED",
            "timestamps": {"committed_at": _timestamp(checkpoint.created_at)},
            "counts": _counts(checkpoint.committed_object_refs),
            "reason_code": None,
            "safe_message": None,
        }
        append_domain_event(
            self._db,
            user_id=run.user_id,
            aggregate_type="task",
            aggregate_id=run.task_id,
            event_type="run.checkpoint_committed",
            aggregate_version=checkpoint.id,
            payload=payload,
            actor_type="system",
            run_id=run.id,
            node_run_id=node.id if node is not None else None,
        )
        self._db.commit()

    def _resolve_attempt(
        self, run_id: int, unit: ExecutionUnit, attempt: int
    ) -> tuple[Run, NodeRun, NodeAttempt, bool]:
        run = self._db.get(Run, run_id)
        if run is None:
            raise ValueError("run not found")
        node_id = unit.node_id or f"unit-{unit.index}"
        node = self._nodes.get_or_create(
            user_id=run.user_id,
            run_id=run.id,
            task_id=run.task_id,
            node_id=node_id,
            node_type=unit.node_type or unit.unit_type,
            position=unit.index,
            input_fingerprint=unit.input_fingerprint,
        )
        existing = self._db.scalar(
            select(NodeAttempt).where(
                NodeAttempt.user_id == run.user_id,
                NodeAttempt.node_run_id == node.id,
                NodeAttempt.attempt == attempt,
            )
        )
        node_attempt = self._attempts.get_or_create(
            user_id=run.user_id, node_run_id=node.id, attempt=attempt
        )
        return run, node, node_attempt, existing is None

    def _append(
        self,
        *,
        run: Run,
        node: NodeRun,
        attempt: NodeAttempt,
        event_type: str,
        state: str,
        counts: dict[str, int | float],
        reason_code: str | None,
        safe_message: str | None,
    ) -> None:
        payload = {
            "schema_version": 1,
            "task_id": run.task_id,
            "run_id": run.id,
            "plan_version": run.plan_version,
            "node_id": node.node_id,
            "node_type": node.node_type,
            "attempt": attempt.attempt,
            "state": state,
            "timestamps": {
                "started_at": _timestamp(attempt.started_at),
                "finished_at": _timestamp(attempt.finished_at),
            },
            "counts": counts,
            "reason_code": reason_code,
            "safe_message": (safe_message or "")[:500] or None,
        }
        append_domain_event(
            self._db,
            user_id=run.user_id,
            aggregate_type="task",
            aggregate_id=run.task_id,
            event_type=event_type,
            aggregate_version=node.version,
            payload=payload,
            actor_type="system",
            run_id=run.id,
            node_run_id=node.id,
        )
