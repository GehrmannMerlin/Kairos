"""M-07 task lifecycle activities (DB side effects live here, never in the workflow)."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.domain.errors import IllegalTransitionError, StaleVersionError
from app.domain.repository import (
    CheckpointRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.domain.service import DomainService
from app.infra.deps import get_session_factory


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


@activity.defn
async def ensure_run_started(inp: EnsureRunStartedInput) -> EnsureRunStartedResult:
    session = get_session_factory()()
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
        # M-16 Level 1+2 task admission（D-071）：无全局/单用户 slot → 等待（非失败），
        # 任务保持 QUEUED。幂等：重放/重复调度重新检查。
        from app.config import get_settings
        from app.reliability.admission import ResourceAdmission
        from app.reliability.capacity import capacity_from_settings

        slot = ResourceAdmission(
            session, capacity_from_settings(get_settings())
        ).try_acquire_task_slot(user_id=inp.user_id, holder_id=f"run{inp.run_id}")
        if not slot.granted:
            return EnsureRunStartedResult(
                inp.run_id, started=False, waiting_reason=slot.reason,
                retry_after_seconds=slot.retry_after_seconds,
            )
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        try:
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="start",
                expected_version=task.version,
                actor_type="system",
                reason="task_workflow_started",
            )
        except StaleVersionError:
            task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
            if task.state != "RUNNING":
                raise
        run.state = "running"
        run.started_at = _utcnow()
        session.add(run)
        session.commit()
        return EnsureRunStartedResult(inp.run_id, started=True)
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
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        with contextlib.suppress(IllegalTransitionError):
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="mark_cancelled",
                expected_version=task.version,
                actor_type="system",
                reason="workflow_cancelled",
            )
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "cancelled"
        run.finished_at = _utcnow()
        session.commit()
        _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
    finally:
        session.close()


@dataclass
class FailRunInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def fail_run(inp: FailRunInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        with contextlib.suppress(IllegalTransitionError):
            # 已在终态（如 FAILED/COMPLETED）时视为幂等成功
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="fail",
                expected_version=task.version,
                actor_type="system",
                reason="workflow_failed",
            )
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "failed"
        run.finished_at = _utcnow()
        session.commit()
        _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
    finally:
        session.close()


@dataclass
class CompleteRunInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def complete_run(inp: CompleteRunInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        DomainService(TaskRepository(session)).transition_task(
            user_id=inp.user_id,
            task_id=inp.task_id,
            command="complete",
            expected_version=task.version,
            actor_type="system",
            reason="workflow_completed",
        )
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "completed"
        run.finished_at = _utcnow()
        session.commit()
        _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
    finally:
        session.close()


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
        existing = CheckpointRepository(session).find_by_batch(inp.run_id, inp.batch_identity)
        if existing is not None:
            if existing.input_fingerprint != inp.input_fingerprint:
                from app.domain.errors import DomainError

                raise DomainError("相同批次身份但输入指纹不同")
            return CommitCheckpointResult(existing.id, reused=True)
        row = await asyncio.to_thread(
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
        )
        return CommitCheckpointResult(row.id, reused=False)
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
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        with contextlib.suppress(IllegalTransitionError):
            # 已在终态（如 PARTIALLY_COMPLETED）时视为幂等成功
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="mark_partial",
                expected_version=task.version,
                actor_type="system",
                reason="partial_completion",
            )
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "partially_completed"
        run.finished_at = _utcnow()
        session.commit()
        _release_task_slot(session, user_id=inp.user_id, run_id=inp.run_id)
    finally:
        session.close()
