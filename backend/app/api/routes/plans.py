"""Plan API routes: 生成（含自动启动）+ 摘要查询（M-08 / D-038）。

Route 只做 auth/DTO/response mapping；生成/校验/持久化/启动语义在
PlanGenerationService + PlanService。owner-safe：无权/不存在 → 404。

D-038：合法低风险 Plan 自动启动，不弹二次 Plan 确认 Modal；Plan 可查看。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.agents.plan_service import PlanGenerationService
from app.api.schemas import (
    PlanGenerateCommand,
    PlanGenerateResponse,
    PlanListResponse,
    PlanSummaryDto,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.errors import DomainError, StaleVersionError
from app.domain.repository import SpecVersionRepository, TaskRepository
from app.infra.deps import get_db
from app.infra.temporal import get_temporal_client
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanValidationResult
from app.plan.service import PlanService, plan_fingerprint
from app.providers.deps import get_credential_vault, get_provider_service
from app.providers.service import ProviderService
from app.workflows.starter import TaskWorkflowStarter

router = APIRouter(prefix="/tasks", tags=["plans"])


def get_plan_generation_service(
    provider_service: ProviderService = Depends(get_provider_service),
    vault: Any = Depends(get_credential_vault),
) -> PlanGenerationService:
    return PlanGenerationService(
        provider_service=provider_service, vault=vault, registry=NodeRegistry()
    )


@dataclass
class _NoopStarter:
    """Plan 摘要查询不需要启动 Workflow；占位满足 PlanService 构造签名。"""

    async def submit_validated_plan(self, **kw):
        from app.workflows.starter import RunStartedResult

        return RunStartedResult(run_id=0, workflow_id="")


def _summary_service(db: DbSession) -> PlanService:
    return PlanService(db, starter=_NoopStarter())


@router.post("/{task_id}/plan", response_model=PlanGenerateResponse)
async def generate_plan(
    task_id: int,
    cmd: PlanGenerateCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    generation: PlanGenerationService = Depends(get_plan_generation_service),
) -> PlanGenerateResponse:
    """Spec confirmed → generate plan → validate → persist → auto-start if legal.

    仅当 Plan 判定为 VALID / REQUIRES_APPROVAL 时启动 Workflow；REQUIRES_NEW_SPEC /
    INVALID / PROHIBITED 只持久化并返回状态，不启动执行。
    """
    # owner-safe：Task 与 Spec 都必须属于当前用户
    TaskRepository(db).get_owned(user.id, task_id)
    spec = SpecVersionRepository(db).get_version(user.id, task_id, cmd.spec_version)
    if spec.confirmed_at is None:
        raise DomainError("采集方案尚未确认，不能生成计划")

    task = TaskRepository(db).get_owned(user.id, task_id)
    if task.version != cmd.expected_version:
        raise StaleVersionError("任务已被其他操作修改")

    from app.domain.task_types import TaskType

    task_type = TaskType(spec.payload.get("task_type") or "SPECIFIED_SOURCE")
    outcome = await generation.generate_for_task(
        user=user, spec_payload=spec.payload, task_type=task_type
    )

    registry_versions = {d.node_type.value: d.definition_version for d in NodeRegistry().all()}
    fingerprint = plan_fingerprint(outcome.graph.model_dump(mode="json"), registry_versions)

    can_start = outcome.validation_result in (
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
    )

    # Temporal client 懒创建：不可用时 Plan 已持久化，不阻塞响应（与 M-07 command route 一致）
    import logging

    logger = logging.getLogger(__name__)
    run_id: int | None = None
    workflow_id: str | None = None
    try:
        client = await get_temporal_client()
        service = PlanService(db, starter=TaskWorkflowStarter(client))
        row = service.persist_plan(
            user_id=user.id,
            task_id=task_id,
            spec_version=cmd.spec_version,
            graph=outcome.graph.model_dump(mode="json"),
            validation_status=outcome.validation_result.value,
            fingerprint_value=fingerprint,
            registry_versions=registry_versions,
            model_config_id=outcome.audit.get("model_config_id"),
            model_config_version=outcome.audit.get("model_config_version"),
        )
        if can_start:
            run_id, workflow_id = await service.auto_start(
                user_id=user.id,
                task_id=task_id,
                spec_version=cmd.spec_version,
                plan_version=row.version,
            )
    except Exception:
        logger.warning(
            "Temporal client unavailable for task %s; plan generated but workflow not started",
            task_id,
            exc_info=True,
        )
        row = PlanService(db, starter=_NoopStarter()).persist_plan(
            user_id=user.id,
            task_id=task_id,
            spec_version=cmd.spec_version,
            graph=outcome.graph.model_dump(mode="json"),
            validation_status=outcome.validation_result.value,
            fingerprint_value=fingerprint,
            registry_versions=registry_versions,
            model_config_id=outcome.audit.get("model_config_id"),
            model_config_version=outcome.audit.get("model_config_version"),
        )
    return PlanGenerateResponse(
        task_id=task_id,
        plan_version=row.version,
        validation_status=outcome.validation_result.value,
        node_count=len(outcome.graph.nodes),
        run_id=run_id,
        workflow_id=workflow_id,
    )


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
