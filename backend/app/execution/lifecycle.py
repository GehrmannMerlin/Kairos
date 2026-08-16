"""Persist safe, idempotent node execution lifecycle facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.activities.execution_seam import ExecutionUnit
from app.domain.models import Checkpoint, DomainEvent, IdempotencyKey, NodeAttempt, NodeRun, Run
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
_LIFECYCLE_STATUSES = frozenset(_TERMINAL_EVENT_TYPES) | {"RUNNING"}
_SAFE_REASON_CODES = frozenset(
    {"INTERNAL", "NETWORK", "NODE_EXECUTOR_UNAVAILABLE", "INVALID_LIFECYCLE_STATUS"}
)
_SAFE_MESSAGES = frozenset({"fetch completed"})
_COUNT_KEYS = {
    "fetched",
    "browser_pending",
    "failed",
    "discovered",
    "extracted",
    "normalized",
    "deduplicated",
    "validated",
    "records",
    "artifacts",
    "eligible",
    "terminal",
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
        if key in _COUNT_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _safe_error_fields(
    status: str, error_code: str | None, safe_message: str | None
) -> tuple[str, str | None, str | None]:
    """Fail closed for unknown lifecycle input and never persist arbitrary text."""
    if status not in _LIFECYCLE_STATUSES:
        return "FAILED", "INVALID_LIFECYCLE_STATUS", None
    code = error_code if error_code in _SAFE_REASON_CODES else None
    message = safe_message if safe_message in _SAFE_MESSAGES else None
    return status, code, message


class ExecutionLifecycleRecorder:
    """Writes a single lifecycle command and its event in one transaction."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._nodes = NodeRunRepository(db)
        self._attempts = NodeAttemptRepository(db)
        self._runs = RunRepository(db)

    def start_attempt(self, *, run_id: int, unit: ExecutionUnit, attempt: int) -> LifecycleAttempt:
        run, node, node_attempt, _ = self._resolve_attempt(run_id, unit, attempt)
        now = _utcnow()
        claimed = self._db.execute(
            update(NodeAttempt)
            .where(NodeAttempt.id == node_attempt.id, NodeAttempt.status == "PENDING")
            .values(status="RUNNING", started_at=now)
        )
        if getattr(claimed, "rowcount", 0) == 1:
            self._db.execute(
                update(NodeRun)
                .where(NodeRun.id == node.id)
                .values(
                    state="RUNNING",
                    started_at=func.coalesce(NodeRun.started_at, now),
                    finished_at=None,
                    version=NodeRun.version + 1,
                )
            )
            self._db.flush()
            self._db.refresh(node)
            self._db.refresh(node_attempt)
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
        else:
            self._db.refresh(node_attempt)
            self._db.refresh(node)
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
        status, error_code, safe_message = _safe_error_fields(status, error_code, safe_message)
        now = _utcnow()
        event_type = _TERMINAL_EVENT_TYPES[status]
        node_state = _NODE_STATES[status]
        attempt_status = "FAILED" if status in {"FAILED", "NODE_EXECUTOR_UNAVAILABLE"} else status
        if event_type != "run.node_progress":
            # The conditional write is the terminal-result linearization point.
            # A stale worker cannot overwrite the winner or emit a second fact.
            claimed = self._db.execute(
                update(NodeAttempt)
                .where(NodeAttempt.id == node_attempt.id, NodeAttempt.finished_at.is_(None))
                .values(
                    status=attempt_status,
                    error_code=error_code,
                    error_summary=(safe_message or "")[:500] or None,
                    started_at=func.coalesce(NodeAttempt.started_at, now),
                    finished_at=now,
                )
            )
            if claimed.rowcount != 1:
                self._db.refresh(node_attempt)
                self._db.refresh(node)
                return LifecycleAttempt(node_run_id=node.id, node_attempt_id=node_attempt.id)
            newer_attempt_exists = (
                select(NodeAttempt.id)
                .where(
                    NodeAttempt.node_run_id == node.id,
                    NodeAttempt.attempt > node_attempt.attempt,
                )
                .exists()
            )
            self._db.execute(
                update(NodeRun)
                .where(NodeRun.id == node.id, ~newer_attempt_exists)
                .values(state=node_state, finished_at=now, version=NodeRun.version + 1)
            )
            self._db.flush()
            self._db.refresh(node_attempt)
            self._db.refresh(node)
        else:
            node_attempt.status = attempt_status
            node_attempt.error_code = error_code
            node_attempt.error_summary = (safe_message or "")[:500] or None
            node_attempt.started_at = node_attempt.started_at or now
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
        if append_checkpoint_event(self._db, checkpoint):
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


def append_checkpoint_event(db: Any, checkpoint: Checkpoint) -> bool:
    """Append the single event for a checkpoint; the caller owns the commit."""
    existing_events = db.scalars(
        select(DomainEvent).where(
            DomainEvent.run_id == checkpoint.run_id,
            DomainEvent.event_type == "run.checkpoint_committed",
        )
    )
    if any(
        (event.payload or {}).get("checkpoint_id") == checkpoint.id for event in existing_events
    ):
        return False
    # DomainEvent has no natural unique key for a JSON checkpoint identity. Reuse
    # the existing durable uniqueness contract as a transaction-scoped claim.
    # The nested savepoint protects the caller's transaction after a lost race.
    try:
        with db.begin_nested():
            claim = IdempotencyKey(
                user_id=checkpoint.user_id,
                operation="checkpoint_event",
                idempotency_key=f"checkpoint:{checkpoint.id}",
                payload_fingerprint=checkpoint.input_fingerprint,
                result_ref_type="checkpoint",
                result_ref_id=checkpoint.id,
            )
            db.add(claim)
            db.flush()
            run = RunRepository(db).get_owned(checkpoint.user_id, checkpoint.run_id)
            node = (
                NodeRunRepository(db).get_owned(checkpoint.user_id, checkpoint.node_run_id)
                if checkpoint.node_run_id is not None
                else None
            )
            append_domain_event(
                db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="run.checkpoint_committed",
                aggregate_version=checkpoint.id,
                payload={
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
                    "checkpoint_id": checkpoint.id,
                },
                actor_type="system",
                run_id=run.id,
                node_run_id=node.id if node is not None else None,
            )
            db.flush()
    except IntegrityError:
        # A competing transaction committed the claim; refetching after the
        # savepoint rollback is safe and leaves the caller's outer transaction live.
        winner = db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == checkpoint.user_id,
                IdempotencyKey.operation == "checkpoint_event",
                IdempotencyKey.idempotency_key == f"checkpoint:{checkpoint.id}",
            )
        )
        if winner is None:
            raise
        return False
    return True
