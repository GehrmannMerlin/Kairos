"""Plan API routes: 生成（含自动启动）+ 摘要查询（M-08 / D-038）。

Route 只做 auth/DTO/response mapping；生成/校验/持久化/启动语义在
PlanGenerationService + PlanService。owner-safe：无权/不存在 → 404。

D-038：合法低风险 Plan 自动启动，不弹二次 Plan 确认 Modal；Plan 可查看。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession
from temporalio.service import RPCError

from app.agents.plan_service import PlanGenerationService
from app.api.schemas import (
    PlanGenerateCommand,
    PlanGenerateResponse,
    PlanListResponse,
    PlanSummaryDto,
    ReplanCommand,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.config import Settings, get_settings
from app.domain.errors import (
    DomainError,
    PlanGenerationTimeoutError,
    PlanStartFailedError,
    StaleVersionError,
)
from app.domain.repository import PlanVersionRepository, SpecVersionRepository, TaskRepository
from app.infra.deps import get_db
from app.infra.temporal import get_temporal_client
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanValidationResult
from app.plan.service import PlanService, PreparedPlanStart, plan_fingerprint
from app.providers.deps import get_credential_vault, get_provider_service
from app.providers.inference_telemetry import emit_lifecycle_event
from app.providers.service import ProviderService
from app.workflows.starter import TaskWorkflowStarter

router = APIRouter(prefix="/tasks", tags=["plans"])
TemporalClientFactory = Callable[[], Awaitable[Any]]


def get_plan_generation_service(
    provider_service: ProviderService = Depends(get_provider_service),
    vault: Any = Depends(get_credential_vault),
    settings: Settings = Depends(get_settings),
) -> PlanGenerationService:
    return PlanGenerationService(
        provider_service=provider_service,
        vault=vault,
        registry=NodeRegistry(),
        settings=settings,
    )


def get_temporal_client_factory() -> TemporalClientFactory:
    """Defer Temporal connection until after the plan and pending run are durable."""

    return get_temporal_client


def _validator_issue_summaries(issues: list[Any]) -> list[dict]:
    return [
        issue.model_dump(mode="json", exclude_none=True, exclude={"expected_schema"})
        for issue in issues
    ]


def _persisted_plan_context(
    *, row: Any, prepared: PreparedPlanStart | None, node_count: int, preflight: Any | None = None
) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "plan_version": row.version,
        "validation_status": row.validation_status,
        "node_count": node_count,
        "run_id": prepared.run_id if prepared is not None else None,
        "workflow_id": prepared.workflow_id if prepared is not None else None,
        "run_state": prepared.run_state if prepared is not None else None,
        "start_recoverable": prepared is not None,
        "validator_issues": (row.payload or {}).get("validator_issues", []),
        "preflight_status": preflight.status.value if preflight is not None else None,
        "preflight_issues": (
            PlanService._preflight_issue_payloads(preflight.issues) if preflight is not None else []
        ),
    }


def _plan_response(
    *, row: Any, prepared: PreparedPlanStart | None, node_count: int, preflight: Any | None = None
) -> PlanGenerateResponse:
    context = _persisted_plan_context(
        row=row, prepared=prepared, node_count=node_count, preflight=preflight
    )
    context["start_recoverable"] = False
    return PlanGenerateResponse(**context)


def _summary_service(db: DbSession) -> PlanService:
    return PlanService(db, starter=None)


@router.post("/{task_id}/plan", response_model=PlanGenerateResponse)
async def generate_plan(
    task_id: int,
    cmd: PlanGenerateCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    generation: PlanGenerationService = Depends(get_plan_generation_service),
    settings: Settings = Depends(get_settings),
    temporal_client_factory: TemporalClientFactory = Depends(get_temporal_client_factory),
) -> PlanGenerateResponse:
    """Spec confirmed → generate plan → validate → persist → auto-start if legal.

    仅当 Plan 判定为 VALID / REQUIRES_APPROVAL 时启动 Workflow；REQUIRES_NEW_SPEC /
    PROHIBITED 只持久化并返回状态，不启动执行。最终 INVALID 会在生成服务中类型化失败。
    """
    # owner-safe：Task 与 Spec 都必须属于当前用户
    TaskRepository(db).get_owned(user.id, task_id)
    spec = SpecVersionRepository(db).get_version(user.id, task_id, cmd.spec_version)
    if spec.confirmed_at is None:
        raise DomainError("采集方案尚未确认，不能生成计划")

    initial_task = TaskRepository(db).get_owned(user.id, task_id)
    if initial_task.version != cmd.expected_version:
        raise StaleVersionError("任务已被其他操作修改")

    from app.domain.task_types import TaskType

    timeout_scope = asyncio.timeout(settings.plan_lifecycle_timeout_seconds)
    try:
        async with timeout_scope:
            task_type = TaskType(spec.payload.get("task_type") or "SPECIFIED_SOURCE")
            outcome = await generation.generate_for_task(
                user=user,
                task_id=task_id,
                spec_version=cmd.spec_version,
                spec_payload=spec.payload,
                task_type=task_type,
            )
            registry_versions = {
                definition.node_type.value: definition.definition_version
                for definition in NodeRegistry().all()
            }
            graph_payload = outcome.graph.model_dump(mode="json")
            service = PlanService(db, starter=None)
            persistence_started = perf_counter()
            row = service.persist_plan(
                user_id=user.id,
                task_id=task_id,
                spec_version=cmd.spec_version,
                graph=graph_payload,
                validation_status=outcome.validation_result.value,
                fingerprint_value=plan_fingerprint(graph_payload, registry_versions),
                registry_versions=registry_versions,
                model_config_id=outcome.audit.get("model_config_id"),
                model_config_version=outcome.audit.get("model_config_version"),
                validation_issues=_validator_issue_summaries(outcome.issues),
                expected_task_version=cmd.expected_version,
            )
            issue_codes = tuple(sorted({issue.code for issue in outcome.issues}))
            emit_lifecycle_event(
                "plan.persisted",
                elapsed_ms=int((perf_counter() - persistence_started) * 1000),
                response_status=row.validation_status,
                plan_version=row.version,
                issue_codes=issue_codes,
            )

            can_start = outcome.validation_result in (
                PlanValidationResult.VALID,
                PlanValidationResult.REQUIRES_APPROVAL,
            )
            prepared: PreparedPlanStart | None = None
            preflight: Any | None = None
            if can_start:
                preflight = service.require_ready_preflight(
                    user_id=user.id,
                    task_id=task_id,
                    spec_version=cmd.spec_version,
                    plan_version=row.version,
                    settings=settings,
                )
                prepared = service.prepare_start(
                    user_id=user.id,
                    task_id=task_id,
                    spec_version=cmd.spec_version,
                    plan_version=row.version,
                )
                workflow_start_started = perf_counter()
                try:
                    temporal_client = await temporal_client_factory()
                    await service.dispatch_prepared_start(
                        prepared,
                        starter=TaskWorkflowStarter(temporal_client, settings),
                    )
                except RPCError as exc:
                    emit_lifecycle_event(
                        "plan.workflow_start_finished",
                        elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
                        response_status="rpc_error",
                        plan_version=row.version,
                        run_state=prepared.run_state,
                    )
                    raise PlanStartFailedError(
                        "计划已保存，但工作流服务暂时不可用；可安全重试启动",
                        context=_persisted_plan_context(
                            row=row,
                            prepared=prepared,
                            node_count=len(outcome.graph.nodes),
                            preflight=preflight,
                        ),
                    ) from exc
                except Exception:
                    emit_lifecycle_event(
                        "plan.workflow_start_finished",
                        elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
                        response_status="internal_error",
                        plan_version=row.version,
                        run_state=prepared.run_state,
                    )
                    raise
                emit_lifecycle_event(
                    "plan.workflow_start_finished",
                    elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
                    response_status="success",
                    plan_version=row.version,
                    run_state=prepared.run_state,
                )

            return _plan_response(
                row=row,
                prepared=prepared,
                node_count=len(outcome.graph.nodes),
                preflight=preflight,
            )
    except TimeoutError as exc:
        if timeout_scope.expired():
            raise PlanGenerationTimeoutError(
                "计划生成生命周期超过服务端时限，请先刷新任务状态"
            ) from exc
        raise


@router.post(
    "/{task_id}/plans/{plan_version}/start",
    response_model=PlanGenerateResponse,
)
async def start_persisted_plan(
    task_id: int,
    plan_version: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    temporal_client_factory: TemporalClientFactory = Depends(get_temporal_client_factory),
) -> PlanGenerateResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    row = PlanVersionRepository(db).get_version(user.id, task_id, plan_version)
    service = PlanService(db, starter=None)
    preflight = service.require_ready_preflight(
        user_id=user.id,
        task_id=task_id,
        spec_version=row.spec_version,
        plan_version=row.version,
        settings=settings,
    )
    prepared = service.prepare_start(
        user_id=user.id,
        task_id=task_id,
        spec_version=row.spec_version,
        plan_version=row.version,
    )
    graph = (row.payload or {}).get("graph", {})
    node_count = len(graph.get("nodes", []))
    workflow_start_started = perf_counter()
    try:
        temporal_client = await temporal_client_factory()
        await service.dispatch_prepared_start(
            prepared,
            starter=TaskWorkflowStarter(temporal_client, settings),
        )
    except RPCError as exc:
        emit_lifecycle_event(
            "plan.workflow_start_finished",
            elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
            response_status="rpc_error",
            plan_version=row.version,
            run_state=prepared.run_state,
        )
        raise PlanStartFailedError(
            "计划已保存，但工作流服务暂时不可用；可安全重试启动",
            context=_persisted_plan_context(
                row=row,
                prepared=prepared,
                node_count=node_count,
                preflight=preflight,
            ),
        ) from exc
    except Exception:
        emit_lifecycle_event(
            "plan.workflow_start_finished",
            elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
            response_status="internal_error",
            plan_version=row.version,
            run_state=prepared.run_state,
        )
        raise
    emit_lifecycle_event(
        "plan.workflow_start_finished",
        elapsed_ms=int((perf_counter() - workflow_start_started) * 1000),
        response_status="success",
        plan_version=row.version,
        run_state=prepared.run_state,
    )
    return _plan_response(row=row, prepared=prepared, node_count=node_count, preflight=preflight)


@router.post("/{task_id}/plans/replan", response_model=PlanSummaryDto)
def replan_plan(
    task_id: int,
    cmd: ReplanCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> PlanSummaryDto:
    """Replan vN+1（执行策略层）。改变 Spec 边界的 replan 被 Validator 拒绝为
    REQUIRES_NEW_SPEC，不能仅 Approval 放行（D-007 审计要求）。"""
    TaskRepository(db).get_owned(user.id, task_id)
    task = TaskRepository(db).get_owned(user.id, task_id)
    if task.version != cmd.expected_version:
        raise StaleVersionError("任务已被其他操作修改")

    from app.plan.diff import PlanDiff
    from app.plan.schemas import PlanGraphDraft
    from app.plan.validator import validate_plan

    parent = PlanVersionRepository(db).latest_version(user.id, task_id)
    if parent is None:
        raise DomainError("没有可重规划的 PlanVersion")

    new_graph = PlanGraphDraft.model_validate(cmd.graph)
    spec = SpecVersionRepository(db).get_version(user.id, task_id, parent.spec_version)
    outcome = validate_plan(
        new_graph, spec.payload, NodeRegistry(), spec_version=parent.spec_version
    )
    if outcome.result == PlanValidationResult.REQUIRES_NEW_SPEC:
        raise DomainError("重规划改变了 Spec 边界，需创建新的采集方案版本")

    old_graph = PlanGraphDraft.model_validate((parent.payload or {}).get("graph", {}))
    diff = PlanDiff.compute(old_graph, new_graph)

    registry_versions = {d.node_type.value: d.definition_version for d in NodeRegistry().all()}
    fingerprint = plan_fingerprint(new_graph.model_dump(mode="json"), registry_versions)

    service = _summary_service(db)
    row = service.create_replan(
        user_id=user.id,
        task_id=task_id,
        spec_version=parent.spec_version,
        graph=new_graph.model_dump(mode="json"),
        fingerprint_value=fingerprint,
        registry_versions=registry_versions,
        trigger_reason=cmd.trigger_reason,
        replan_evidence_refs=cmd.evidence_refs,
        diff_summary=diff.model_dump(mode="json"),
    )
    summary = service.get_plan_summary(user_id=user.id, task_id=task_id, plan_version=row.version)
    return PlanSummaryDto(**summary)


@router.get("/{task_id}/plans", response_model=PlanListResponse)
def list_plans(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> PlanListResponse:
    service = _summary_service(db)
    summaries = service.list_plan_summaries(user_id=user.id, task_id=task_id)
    return PlanListResponse(task_id=task_id, plans=[PlanSummaryDto(**s) for s in summaries])


@router.get("/{task_id}/plans/{plan_version}", response_model=PlanSummaryDto)
def get_plan_summary(
    task_id: int,
    plan_version: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> PlanSummaryDto:
    service = _summary_service(db)
    summary = service.get_plan_summary(user_id=user.id, task_id=task_id, plan_version=plan_version)
    return PlanSummaryDto(**summary)
