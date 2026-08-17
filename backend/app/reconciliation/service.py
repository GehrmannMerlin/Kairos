"""Minimal terminal reconciliation for lost/abandoned TaskWorkflows (P0-4).

The workflow already closes every terminal path (``complete_run``/``mark_partial``/
``fail_run``/``mark_cancelled``) with a CAS-based claim inside ``_finish_run``. This module
closes the one remaining window: a workflow terminated at the Temporal layer (external
``terminate``/``cancel``, worker lost before the terminal activity, or retention eviction)
leaves the PostgreSQL Run stuck in ``running`` forever.

The reconciler is **not** a new business state source. It only detects that the Temporal
execution truth is already terminal (or gone) while PostgreSQL is still ``running``, derives
the single legal business outcome, and re-applies the existing terminal command through the
same CAS + ``DomainService`` path — so a concurrent workflow that is still alive and wins the
claim is never overwritten.

Safety: default ``dry_run``; only touches runs whose workflow is provably terminal/absent;
owner-scoped; never writes raw SQL state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.domain.models import CompletionDecision, Run
from app.infra.deps import get_session_factory

# Temporal WorkflowExecutionStatus names that prove the execution is over.
_TERMINAL_TEMPORAL_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
)

DEFAULT_STALE_AFTER_SECONDS = 3600  # 1 hour; active workflows are skipped regardless.

WorkflowStatusFn = Callable[[str], Awaitable[str | None]]
ApplyFn = Callable[[str, "StaleRun"], Awaitable[None]]
SessionFactory = Callable[[], Any]


@dataclass(frozen=True)
class StaleRun:
    run_id: int
    task_id: int
    user_id: int
    is_partial: bool | None  # None when no CompletionDecision was ever persisted


def resolve_terminal_command(*, temporal_status: str | None, is_partial: bool | None) -> str:
    """Map a terminal Temporal status (+ persisted completion fact) to one terminal command.

    - ``CANCELED`` → ``mark_cancelled`` (user/collaborative cancel).
    - ``COMPLETED`` → ``complete`` or ``mark_partial`` per the persisted CompletionDecision;
      if no decision was persisted the workflow completed without resolving completion, so we
      fail closed rather than guess a data outcome.
    - anything else (``FAILED``/``TERMINATED``/``TIMED_OUT``/absent) → ``fail``.
    """
    status = (temporal_status or "").upper()
    if status == "CANCELED":
        return "mark_cancelled"
    if status == "COMPLETED":
        if is_partial is None:
            return "fail"
        return "mark_partial" if is_partial else "complete"
    return "fail"


def query_stale_runs(session: Any, *, stale_after_seconds: int) -> list[StaleRun]:
    """Return ``running`` Runs whose workflow started before the staleness cutoff."""
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    rows = session.scalars(select(Run).where(Run.state == "running", Run.started_at < cutoff)).all()
    out: list[StaleRun] = []
    for run in rows:
        decision = session.scalar(
            select(CompletionDecision)
            .where(
                CompletionDecision.user_id == run.user_id,
                CompletionDecision.task_id == run.task_id,
                CompletionDecision.run_id == run.id,
            )
            .order_by(CompletionDecision.id.desc())
            .limit(1)
        )
        out.append(
            StaleRun(
                run_id=run.id,
                task_id=run.task_id,
                user_id=run.user_id,
                is_partial=decision.is_partial if decision is not None else None,
            )
        )
    return out


async def _apply_command(command: str, run: StaleRun) -> None:
    from app.activities.task_execution import (
        CompleteRunInput,
        FailRunInput,
        MarkCancelledInput,
        MarkPartialInput,
        complete_run,
        fail_run,
        mark_cancelled,
        mark_partial,
    )

    if command == "mark_cancelled":
        await mark_cancelled(
            MarkCancelledInput(task_id=run.task_id, user_id=run.user_id, run_id=run.run_id)
        )
    elif command == "complete":
        await complete_run(
            CompleteRunInput(task_id=run.task_id, user_id=run.user_id, run_id=run.run_id)
        )
    elif command == "mark_partial":
        await mark_partial(
            MarkPartialInput(task_id=run.task_id, user_id=run.user_id, run_id=run.run_id)
        )
    else:
        await fail_run(
            FailRunInput(
                task_id=run.task_id,
                user_id=run.user_id,
                run_id=run.run_id,
                error_code="WORKFLOW_LOST",
            )
        )


async def reconcile_stale_runs(
    *,
    workflow_status_fn: WorkflowStatusFn,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    dry_run: bool = True,
    session_factory: SessionFactory | None = None,
    apply_fn: ApplyFn | None = None,
) -> list[dict[str, Any]]:
    """Reconcile stale ``running`` runs whose Temporal workflow is terminal or absent.

    ``apply_fn`` injects the mutation for tests; the production default calls the existing
    terminal activities. Returns one record per inspected run for audit/dry-run review.
    """
    factory: SessionFactory = session_factory or get_session_factory
    session = factory()
    try:
        stale = query_stale_runs(session, stale_after_seconds=stale_after_seconds)
    finally:
        session.close()

    results: list[dict[str, Any]] = []
    for run in stale:
        status = await workflow_status_fn(f"task-workflow-{run.task_id}")
        if status is not None and status.upper() not in _TERMINAL_TEMPORAL_STATUSES:
            results.append({"run_id": run.run_id, "action": "skip", "temporal_status": status})
            continue
        command = resolve_terminal_command(temporal_status=status, is_partial=run.is_partial)
        results.append(
            {
                "run_id": run.run_id,
                "task_id": run.task_id,
                "action": command,
                "temporal_status": status,
                "applied": not dry_run,
            }
        )
        if not dry_run:
            await (apply_fn or _apply_command)(command, run)
    return results


__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "StaleRun",
    "query_stale_runs",
    "reconcile_stale_runs",
    "resolve_terminal_command",
]
