"""M-12 resolve_completion activity：Workflow 无更多单元时计算 CompletionDecision 并持久化。

CompletionDecision 与 QualityMetrics 分开表达（模块需求 52）：这里只根据范围/饱和/限制
判定完成，不掺入质量好坏。副作用在 Activity；Workflow 只编排。

CONTINUE（结果不足但仍有搜索轮次）不持久化 CompletionDecision，而是把 typed decision +
机器可读 hints 返回给 Workflow 编排受控重规划；只有终态（COMPLETED / PARTIALLY_COMPLETED）
才持久化。FAILED 由 CompletionIncompleteError 表达（无可用 completed work）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from temporalio import activity

from app.infra.deps import get_session_factory


@dataclass
class ResolveCompletionInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int
    search_round_count: int = 1


@dataclass
class ResolveCompletionResult:
    partial: bool
    status: str
    completion_type: str | None
    qualified_record_count: int
    completion_id: int | None = None
    failure_code: str | None = None
    outcome: str = "COMPLETED"  # COMPLETED | CONTINUE | PARTIALLY_COMPLETED | FAILED
    continue_hints: dict = field(default_factory=dict)


@activity.defn
async def resolve_completion(inp: ResolveCompletionInput) -> ResolveCompletionResult:
    session = get_session_factory()()
    try:
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.domain.models import Run
        from app.domain.repository import IdempotencyRepository, SpecVersionRepository
        from app.validation.completion import (
            CompletionDecisionService,
            CompletionIncompleteError,
        )
        from app.validation.policies import ValidationSettings
        from app.validation.repository import ValidationRepository

        run = session.scalar(select(Run).where(Run.id == inp.run_id, Run.user_id == inp.user_id))
        if run is None:
            return ResolveCompletionResult(
                partial=False,
                status="FAILED",
                completion_type=None,
                qualified_record_count=0,
                failure_code="RUN_NOT_FOUND",
                outcome="FAILED",
            )
        if (
            run.task_id != inp.task_id
            or run.spec_version != inp.spec_version
            or run.plan_version != inp.plan_version
        ):
            return ResolveCompletionResult(
                partial=False,
                status="FAILED",
                completion_type=None,
                qualified_record_count=0,
                failure_code="RUN_IDENTITY_MISMATCH",
                outcome="FAILED",
            )
        spec = SpecVersionRepository(session).get_version(
            inp.user_id, inp.task_id, inp.spec_version
        )
        repo = ValidationRepository(session)
        existing = _find_completion(
            session,
            user_id=inp.user_id,
            task_id=inp.task_id,
            run_id=inp.run_id,
            spec_version=inp.spec_version,
            plan_version=inp.plan_version,
        )
        if existing is not None:
            return _completion_result(existing)
        counts = _count_partitions(
            session,
            user_id=inp.user_id,
            task_id=inp.task_id,
            run_id=inp.run_id,
            spec_version=inp.spec_version,
        )
        qualified = counts.get("passed", 0)
        settings = ValidationSettings()
        fetched_page_count = _count_fetched(
            session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
        )
        runtime_limit_reason = _runtime_limit_reason(spec.payload or {}, fetched_page_count)
        try:
            decision = CompletionDecisionService().decide(
                run=run,
                spec_payload=spec.payload or {},
                partition_counts=counts,
                eligible_url_count=_count_eligible(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                terminal_url_count=_count_terminal(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                fetched_page_count=fetched_page_count,
                record_count=_count_records(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                batch_unique_counts=[],
                qualified_record_count=qualified,
                runtime_limit_reason=runtime_limit_reason,
                user_stopped=False,
                settings=settings,
                access_limited_reason=_access_limited_reason(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                search_round_count=inp.search_round_count,
                max_search_rounds=settings.max_search_rounds,
            )
        except CompletionIncompleteError as exc:
            return ResolveCompletionResult(
                partial=False,
                status="FAILED",
                completion_type=None,
                qualified_record_count=qualified,
                failure_code=exc.code,
                outcome="FAILED",
            )
        if decision.outcome.value == "CONTINUE":
            # 受控继续：不持久化终态，返回 typed decision + hints 给 Workflow 编排 replan。
            return ResolveCompletionResult(
                partial=False,
                status="CONTINUE",
                completion_type=decision.completion_type,
                qualified_record_count=qualified,
                outcome="CONTINUE",
                continue_hints=decision.continue_hints,
            )
        try:
            row = repo.create_completion(
                user_id=inp.user_id,
                task_id=inp.task_id,
                run_id=inp.run_id,
                spec_version=inp.spec_version,
                plan_version=inp.plan_version,
                decision=decision.model_dump(mode="json", exclude={"outcome", "continue_hints"}),
            )
            session.flush()
            IdempotencyRepository(session).create(
                user_id=inp.user_id,
                operation="completion_decision",
                key=f"run:{inp.run_id}:spec:{inp.spec_version}:plan:{inp.plan_version}",
                payload_fingerprint="completion-v1",
                result_ref_type="completion_decision",
                result_ref_id=row.id,
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = _find_completion(
                session,
                user_id=inp.user_id,
                task_id=inp.task_id,
                run_id=inp.run_id,
                spec_version=inp.spec_version,
                plan_version=inp.plan_version,
            )
            if existing is None:
                raise
            row = existing
        session.refresh(row)
        return _completion_result(row)
    finally:
        session.close()


def _completion_result(row) -> ResolveCompletionResult:
    outcome = "PARTIALLY_COMPLETED" if row.is_partial else "COMPLETED"
    return ResolveCompletionResult(
        partial=row.is_partial,
        status=row.status,
        completion_type=row.completion_type,
        qualified_record_count=row.qualified_record_count,
        completion_id=row.id,
        outcome=outcome,
    )


def _find_completion(
    session,
    *,
    user_id: int,
    task_id: int,
    run_id: int,
    spec_version: int,
    plan_version: int,
):
    from sqlalchemy import select

    from app.domain.models import CompletionDecision

    return session.scalar(
        select(CompletionDecision).where(
            CompletionDecision.user_id == user_id,
            CompletionDecision.task_id == task_id,
            CompletionDecision.run_id == run_id,
            CompletionDecision.spec_version == spec_version,
            CompletionDecision.plan_version == plan_version,
        )
    )


def _count_partitions(
    session, *, user_id: int, task_id: int, run_id: int, spec_version: int
) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.domain.models import ValidationResult

    rows = session.execute(
        select(ValidationResult.partition, func.count())
        .where(
            ValidationResult.user_id == user_id,
            ValidationResult.task_id == task_id,
            ValidationResult.run_id == run_id,
            ValidationResult.spec_version_id == spec_version,
        )
        .group_by(ValidationResult.partition)
    ).all()
    return {partition: int(count) for partition, count in rows}


def _count_eligible(session, user_id: int, task_id: int, run_id: int, spec_version: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.run_id == run_id,
                URLResource.spec_version == spec_version,
                URLResource.status.in_(
                    ["READY_FOR_FETCH", "FETCHED", "HANDED_OFF", "SKIPPED", "FETCH_FAILED"]
                ),
            )
        ).scalar()
        or 0
    )


def _count_terminal(session, user_id: int, task_id: int, run_id: int, spec_version: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.run_id == run_id,
                URLResource.spec_version == spec_version,
                URLResource.status.in_(["FETCHED", "HANDED_OFF", "SKIPPED", "FETCH_FAILED"]),
            )
        ).scalar()
        or 0
    )


def _count_fetched(session, user_id: int, task_id: int, run_id: int, spec_version: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.run_id == run_id,
                URLResource.spec_version == spec_version,
                URLResource.status.in_(["FETCHED", "HANDED_OFF"]),
            )
        ).scalar()
        or 0
    )


def _count_records(session, user_id: int, task_id: int, run_id: int, spec_version: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import Record

    return int(
        session.execute(
            select(func.count()).where(
                Record.user_id == user_id,
                Record.task_id == task_id,
                Record.run_id == run_id,
                Record.spec_version == spec_version,
            )
        ).scalar()
        or 0
    )


def _access_limited_reason(
    session, user_id: int, task_id: int, run_id: int, spec_version: int
) -> str | None:
    from sqlalchemy import select

    from app.domain.models import URLResource

    blocked = session.scalar(
        select(URLResource.id).where(
            URLResource.user_id == user_id,
            URLResource.task_id == task_id,
            URLResource.run_id == run_id,
            URLResource.spec_version == spec_version,
            URLResource.status.in_(["SKIPPED", "FETCH_FAILED"]),
        )
    )
    return "access_limited" if blocked is not None else None


def _runtime_limit_reason(spec_payload: dict, fetched_page_count: int) -> str | None:
    """从 Spec advanced_settings 推导非金额运行边界（D-037 / 模块需求 51）。

    仅实现 max_pages 边界（确定性、可稳定判定）；max_duration 需引入稳定耗时事实，
    当前不在 Completion 内求值，避免以不确定时钟影响完成判定。
    """
    advanced = spec_payload.get("advanced_settings") or {}
    max_pages = advanced.get("max_pages")
    if max_pages is not None and fetched_page_count >= int(max_pages):
        return "max_pages_reached"
    return None


__all__ = [
    "ResolveCompletionInput",
    "ResolveCompletionResult",
    "resolve_completion",
]
