"""M-12 resolve_completion activity：Workflow 无更多单元时计算 CompletionDecision 并持久化。

CompletionDecision 与 QualityMetrics 分开表达（模块需求 52）：这里只根据范围/饱和/限制
判定完成，不掺入质量好坏。副作用在 Activity；Workflow 只编排。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.infra.deps import get_session_factory


@dataclass
class ResolveCompletionInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int


@dataclass
class ResolveCompletionResult:
    partial: bool
    status: str
    completion_type: str | None
    qualified_record_count: int
    completion_id: int | None = None
    failure_code: str | None = None


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
                fetched_page_count=_count_fetched(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                record_count=_count_records(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
                batch_unique_counts=[],
                qualified_record_count=qualified,
                runtime_limit_reason=None,
                user_stopped=False,
                settings=ValidationSettings(),
                access_limited_reason=_access_limited_reason(
                    session, inp.user_id, inp.task_id, inp.run_id, inp.spec_version
                ),
            )
        except CompletionIncompleteError as exc:
            return ResolveCompletionResult(
                partial=False,
                status="FAILED",
                completion_type=None,
                qualified_record_count=qualified,
                failure_code=exc.code,
            )
        try:
            row = repo.create_completion(
                user_id=inp.user_id,
                task_id=inp.task_id,
                run_id=inp.run_id,
                spec_version=inp.spec_version,
                plan_version=inp.plan_version,
                decision=decision.model_dump(mode="json"),
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
    return ResolveCompletionResult(
        partial=row.is_partial,
        status=row.status,
        completion_type=row.completion_type,
        qualified_record_count=row.qualified_record_count,
        completion_id=row.id,
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


__all__ = [
    "ResolveCompletionInput",
    "ResolveCompletionResult",
    "resolve_completion",
]
