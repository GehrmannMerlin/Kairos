"""M-07 task lifecycle activities (DB side effects live here, never in the workflow)."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.crawling.errors import FetchErrorCode
from app.domain.errors import IllegalTransitionError, StaleVersionError
from app.domain.models import CompletionDecision, DomainEvent, Run
from app.domain.repository import RunRepository, SpecVersionRepository, TaskRepository
from app.domain.service import DomainService
from app.infra.deps import get_session_factory
from app.observability.execution_metrics import get_execution_metrics
from app.state.events import append_domain_event


def _utcnow() -> datetime:
    return datetime.now(UTC)


_SAFE_EXECUTION_ERROR_CODES = frozenset(
    {
        "INTERNAL",
        "NETWORK",
        "NETWORK_ERROR",
        "RUN_NOT_FOUND",
        "STORAGE_ERROR",
        "NODE_EXECUTOR_UNAVAILABLE",
        "CREDENTIAL_REQUIRED",
        "API_KEY_REQUIRED",
        "ACCESS_DENIED",
        "AUTH_REQUIRED",
        "CAPTCHA_REQUIRED",
        "NOT_FOUND",
        "UNSUPPORTED_RESPONSE",
        "BASE_URL_REQUIRED",
        "INVALID_BASE_URL",
        "RESOURCE_UNAVAILABLE",
        "INVALID_LIFECYCLE_STATUS",
        "WORKFLOW_FAILED",
        "EXECUTION_FAILED",
        "INCOMPLETE_WITHOUT_COMPLETED_WORK",
    }
).union(code.value for code in FetchErrorCode)


def _safe_error_code(error_code: str | None) -> str:
    return error_code if error_code in _SAFE_EXECUTION_ERROR_CODES else "EXECUTION_FAILED"


def _release_task_slot(session, *, user_id: int, run_id: int) -> None:
    """终态释放 task slot（global+user lease）。幂等：无 lease 时为空操作。"""
    from app.config import get_settings
    from app.reliability.admission import ResourceAdmission
    from app.reliability.capacity import capacity_from_settings

    try:
        ResourceAdmission(session, capacity_from_settings(get_settings())).release_task_slot(
            user_id=user_id, holder_id=f"run{run_id}"
        )
    except Exception:
        session.rollback()


@dataclass
class EnsureRunStartedInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int


@dataclass
class EnsureRunStartedResult:
    run_id: int
    started: bool
    waiting_reason: str | None = None  # global_limit | per_user_limit | None
    retry_after_seconds: float = 5.0


class RunSpecNotFrozenError(ApplicationError):
    """Spec 未冻结时稳定业务错误：不允许进入 RUNNING（non-retryable，不重试）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=True)


class RunTerminalError(ApplicationError):
    """Stable terminal identity/conflict failure that Temporal must not retry."""

    def __init__(self, code: str) -> None:
        super().__init__(code, type=code, non_retryable=True)


@activity.defn
async def ensure_run_started(inp: EnsureRunStartedInput) -> EnsureRunStartedResult:
    session = get_session_factory()()
    slot_acquired = False
    try:
        spec = SpecVersionRepository(session).get_version(
            inp.user_id, inp.task_id, inp.spec_version
        )
        if spec.confirmed_at is None:
            raise RunSpecNotFrozenError("采集方案尚未确认，不能启动执行")
        # 摄入 Spec seed_urls 到 URL Frontier（D-068：指定来源基于用户提供 URL 运行）。
        # 放在 run.state 早返回之前：重放/re-run 幂等 upsert 保证 seed 始终存在（D-016）。
        _ingest_seed_urls(session, inp, spec)
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        if run.state != "pending":
            return EnsureRunStartedResult(inp.run_id, started=False)

        # ResourceAdmission owns lease commits, so acquire before the lifecycle
        # transaction; a lost claim or later error releases it before returning.
        from app.config import get_settings
        from app.reliability.admission import ResourceAdmission
        from app.reliability.capacity import capacity_from_settings

        slot = ResourceAdmission(
            session, capacity_from_settings(get_settings())
        ).try_acquire_task_slot(user_id=inp.user_id, holder_id=f"run{inp.run_id}")
        if not slot.granted:
            return EnsureRunStartedResult(
                inp.run_id,
                started=False,
                waiting_reason=slot.reason,
                retry_after_seconds=slot.retry_after_seconds,
            )
        slot_acquired = slot.owned
        # Claim the pending Run before changing task state or appending run.started.
        # The conditional write is the cross-worker linearization point: only its
        # winner owns the task transition and event in this transaction.
        started_at = _utcnow()
        claimed = session.execute(
            update(Run)
            .where(Run.id == inp.run_id, Run.user_id == inp.user_id, Run.state == "pending")
            .values(state="running", started_at=started_at)
        )
        if getattr(claimed, "rowcount", 0) != 1:
            session.rollback()
            # The other Run-claim contender may be using the same idempotent
            # lease pair; never release a run-owned holder after losing CAS.
            slot_acquired = False
            return EnsureRunStartedResult(inp.run_id, started=False)

        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        if task.state == "QUEUED":
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="start",
                expected_version=task.version,
                actor_type="system",
                reason="task_workflow_started",
                commit=False,
            )
        elif task.state != "RUNNING":
            raise StaleVersionError("任务未处于可恢复的启动状态")
        started_event = session.scalar(
            select(DomainEvent).where(
                DomainEvent.run_id == inp.run_id,
                DomainEvent.event_type == "run.started",
            )
        )
        if started_event is None:
            append_domain_event(
                session,
                user_id=inp.user_id,
                aggregate_type="task",
                aggregate_id=inp.task_id,
                event_type="run.started",
                aggregate_version=task.version,
                payload={
                    "schema_version": 1,
                    "task_id": inp.task_id,
                    "run_id": inp.run_id,
                    "spec_version": inp.spec_version,
                    "plan_version": inp.plan_version,
                    "seed_count": len(
                        ((spec.payload or {}).get("source_scope") or {}).get("seed_urls") or []
                    ),
                },
                actor_type="system",
                run_id=inp.run_id,
            )
        session.commit()
        return EnsureRunStartedResult(inp.run_id, started=True)
    except Exception:
        session.rollback()
        if slot_acquired:
            _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
        raise
    finally:
        session.close()


def _ingest_seed_urls(session, inp: EnsureRunStartedInput, spec: Any) -> None:
    from app.discovery.frontier import UrlFrontierRepository
    from app.discovery.models import DiscoveryEvidence, DiscoverySource

    seed_urls = ((spec.payload or {}).get("source_scope") or {}).get("seed_urls") or []
    if not seed_urls:
        return
    frontier = UrlFrontierRepository(session)
    for url in seed_urls:
        frontier.upsert_discovery(
            task_id=inp.task_id,
            user_id=inp.user_id,
            run_id=inp.run_id,
            spec_version=inp.spec_version,
            raw_url=url,
            source=DiscoverySource.USER_SEED,
            evidence=DiscoveryEvidence(source=DiscoverySource.USER_SEED, note="spec_seed"),
        )


@dataclass
class MarkPausedInput:
    task_id: int
    user_id: int


@activity.defn
async def mark_paused(inp: MarkPausedInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        with contextlib.suppress(IllegalTransitionError):
            # 已在 PAUSED（重复信号）视为幂等成功
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="mark_paused",
                expected_version=task.version,
                actor_type="system",
                reason="workflow_stopped",
            )
    finally:
        session.close()


@dataclass
class MarkCancelledInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def mark_cancelled(inp: MarkCancelledInput) -> None:
    await _finish_run(
        inp,
        command="mark_cancelled",
        run_state="cancelled",
        event_type="run.cancelled",
        reason="workflow_cancelled",
    )


@dataclass
class FailRunInput:
    task_id: int
    user_id: int
    run_id: int
    error_code: str | None = None


@activity.defn
async def fail_run(inp: FailRunInput) -> None:
    error_code = _safe_error_code(inp.error_code)
    await _finish_run(
        inp,
        command="fail",
        run_state="failed",
        event_type="run.failed",
        reason=error_code,
        error_code=error_code,
    )


async def _finish_run(
    inp: Any,
    *,
    command: str,
    run_state: str,
    event_type: str,
    reason: str,
    error_code: str | None = None,
) -> None:
    """Atomically persist one terminal task transition and its Run lifecycle fact."""
    session = get_session_factory()()
    try:
        claimed = session.execute(
            update(Run)
            .where(
                Run.id == inp.run_id,
                Run.user_id == inp.user_id,
                Run.task_id == inp.task_id,
                Run.state == "running",
            )
            .values(state=run_state, finished_at=_utcnow())
        )
        # The conditional update is the cross-worker terminal claim. A loser
        # writes neither Task state nor another run lifecycle event.
        if getattr(claimed, "rowcount", 0) != 1:
            session.rollback()
            existing = RunRepository(session).get_owned(inp.user_id, inp.run_id)
            if existing.task_id != inp.task_id:
                raise RunTerminalError("RUN_IDENTITY_MISMATCH")
            if existing.state != run_state:
                raise RunTerminalError("RUN_TERMINAL_CONFLICT")
            _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
            return
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        DomainService(TaskRepository(session)).transition_task(
            user_id=inp.user_id,
            task_id=inp.task_id,
            command=command,
            expected_version=task.version,
            actor_type="system",
            reason=reason,
            commit=False,
        )
        payload = {
            "schema_version": 1,
            "task_id": inp.task_id,
            "run_id": inp.run_id,
            "transition": event_type,
            "outcome_code": error_code or reason,
        }
        if error_code is not None:
            payload["error_code"] = error_code
        append_domain_event(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type=event_type,
            aggregate_version=task.version,
            payload=payload,
            actor_type="system",
            run_id=inp.run_id,
        )
        session.commit()
        _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
        metrics = get_execution_metrics()
        metrics.record_run_terminal(
            state=run_state.upper(),
            outcome_code=error_code or reason,
        )
        if run_state == "partially_completed" and _eligible_count(session, inp) == 0:
            metrics.record_invariant_violation(invariant="eligible_zero_partial")
    finally:
        session.close()


def _eligible_count(session: Any, inp: Any) -> int | None:
    decision = session.scalar(
        select(CompletionDecision)
        .where(
            CompletionDecision.user_id == inp.user_id,
            CompletionDecision.task_id == inp.task_id,
            CompletionDecision.run_id == inp.run_id,
        )
        .order_by(CompletionDecision.id.desc())
        .limit(1)
    )
    if decision is None or not isinstance(decision.scope_completion_metadata, dict):
        return None
    value = decision.scope_completion_metadata.get("eligible_urls")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class CompleteRunInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def complete_run(inp: CompleteRunInput) -> None:
    await _finish_run(
        inp,
        command="complete",
        run_state="completed",
        event_type="run.completed",
        reason="workflow_completed",
    )


@dataclass
class CommitCheckpointInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int
    batch_identity: str
    node_run_id: int | None
    input_fingerprint: str
    committed_refs: dict
    content_hash: str | None


@dataclass
class CommitCheckpointResult:
    checkpoint_id: int
    reused: bool


@activity.defn
async def commit_checkpoint(inp: CommitCheckpointInput) -> CommitCheckpointResult:
    session = get_session_factory()()
    try:
        checkpoint_result = await asyncio.to_thread(
            DomainService(TaskRepository(session)).commit_checkpoint,
            user_id=inp.user_id,
            task_id=inp.task_id,
            run_id=inp.run_id,
            batch_identity=inp.batch_identity,
            spec_version=inp.spec_version,
            plan_version=inp.plan_version,
            node_run_id=inp.node_run_id,
            input_fingerprint=inp.input_fingerprint,
            committed_refs=inp.committed_refs,
            content_hash=inp.content_hash,
            return_reused=True,
        )
        row, reused = cast(tuple[Any, bool], checkpoint_result)
        return CommitCheckpointResult(row.id, reused=reused)
    finally:
        session.close()


@dataclass
class MarkPartialInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def mark_partial(inp: MarkPartialInput) -> None:
    """M-12 部分完成收尾：RUNNING → PARTIALLY_COMPLETED（D-006 部分完成）。

    已提交数据保留（模块需求 50）；不把已 CANCELLED Run 改 COMPLETED，业务状态与
    数据可用性分开表达。
    """
    await _finish_run(
        inp,
        command="mark_partial",
        run_state="partially_completed",
        event_type="run.partially_completed",
        reason="partial_completion",
    )
