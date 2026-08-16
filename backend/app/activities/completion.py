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


@activity.defn
async def resolve_completion(inp: ResolveCompletionInput) -> ResolveCompletionResult:
    session = get_session_factory()()
    try:
        from app.domain.models import Run
        from app.domain.repository import SpecVersionRepository
        from app.validation.completion import CompletionDecisionService
        from app.validation.policies import ValidationSettings
        from app.validation.repository import ValidationRepository

        run = session.get(Run, inp.run_id)
        if run is None:
            return ResolveCompletionResult(
                partial=True,
                status="PARTIALLY_COMPLETED",
                completion_type="access_limited",
                qualified_record_count=0,
            )
        spec = SpecVersionRepository(session).get_version(
            inp.user_id, inp.task_id, inp.spec_version
        )
        repo = ValidationRepository(session)
        counts = repo.count_by_partition(user_id=inp.user_id, task_id=inp.task_id)
        qualified = counts.get("passed", 0)
        decision = CompletionDecisionService().decide(
            run=run,
            spec_payload=spec.payload or {},
            partition_counts=counts,
            eligible_url_count=_count_eligible(session, inp.user_id, inp.task_id),
            terminal_url_count=_count_terminal(session, inp.user_id, inp.task_id),
            fetched_page_count=_count_fetched(session, inp.user_id, inp.task_id),
            record_count=_count_records(session, inp.user_id, inp.task_id),
            batch_unique_counts=[],
            qualified_record_count=qualified,
            runtime_limit_reason=None,
            user_stopped=False,
            settings=ValidationSettings(),
        )
        row = repo.create_completion(
            user_id=inp.user_id,
            task_id=inp.task_id,
            run_id=inp.run_id,
            spec_version=inp.spec_version,
            plan_version=inp.plan_version,
            decision=decision.model_dump(mode="json"),
        )
        session.commit()
        session.refresh(row)
        return ResolveCompletionResult(
            partial=decision.is_partial,
            status=decision.status,
            completion_type=decision.completion_type,
            qualified_record_count=qualified,
            completion_id=row.id,
        )
    finally:
        session.close()


def _count_eligible(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.status.in_(
                    ["READY_FOR_FETCH", "FETCHED", "HANDED_OFF", "SKIPPED", "FETCH_FAILED"]
                ),
            )
        ).scalar()
        or 0
    )


def _count_terminal(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.status.in_(["HANDED_OFF", "SKIPPED", "FETCH_FAILED"]),
            )
        ).scalar()
        or 0
    )


def _count_fetched(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import URLResource

    return int(
        session.execute(
            select(func.count()).where(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.status.in_(["FETCHED", "HANDED_OFF"]),
            )
        ).scalar()
        or 0
    )


def _count_records(session, user_id: int, task_id: int) -> int:
    from sqlalchemy import func, select

    from app.domain.models import Record

    return int(
        session.execute(
            select(func.count()).where(Record.user_id == user_id, Record.task_id == task_id)
        ).scalar()
        or 0
    )


__all__ = [
    "ResolveCompletionInput",
    "ResolveCompletionResult",
    "resolve_completion",
]
