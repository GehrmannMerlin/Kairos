"""M-12 受控重规划 Activity（D-007 / D-013 / 模块需求 43-52）。

CompletionDecision = CONTINUE 时由 TaskWorkflow 编排调用；所有 LLM/网络副作用都在此
Activity（Workflow 保持确定性）。从冻结 PlanVersion 解析用户自己的模型（复用
ExtractionModelResolver 模式），以 continue_hints + 上一轮状态摘要作为修复上下文生成
vN+1 计划，确定性校验通过后持久化并更新 Run.plan_version。

约束：replan 只改执行策略层（搜索词/来源顺序/参数），不改 Spec 边界（D-007）。同一种
不足只有在输入/参数/策略发生有效变化时再次尝试（D-013），continuation 天然携带新上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from temporalio import activity

from app.infra.deps import get_session_factory


@dataclass
class ReplanContinuationInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    current_plan_version: int
    search_round_count: int
    continue_hints: dict = field(default_factory=dict)


@dataclass
class ReplanContinuationResult:
    new_plan_version: int | None = None
    status: str = "OK"  # OK | FAILED
    failure_code: str | None = None


def _namespace_graph(graph: dict, version: int) -> dict:
    """把节点 node_id 按 plan 版本命名空间化，避免 (run_id, node_id) NodeRun 跨版本冲突。

    NodeRun 以 (run_id, node_id) 唯一；同一 Run 内多个 PlanVersion 复用 "n1"/"n2" 会导致
    get_or_create 复用旧节点。这里把 node_id 重写为 ``p{version}_{node_id}`` 并同步
    depends_on / edges 引用，保证每个 PlanVersion 的节点身份唯一。
    """
    nodes = graph.get("nodes", [])
    mapping = {n["node_id"]: f"p{version}_{n['node_id']}" for n in nodes}
    namespaced_nodes = []
    for node in nodes:
        new_node = dict(node)
        new_node["node_id"] = mapping[node["node_id"]]
        new_node["depends_on"] = [mapping.get(d, d) for d in node.get("depends_on", [])]
        namespaced_nodes.append(new_node)
    namespaced_edges = []
    for edge in graph.get("edges", []):
        new_edge = dict(edge)
        new_edge["from_node_id"] = mapping.get(edge["from_node_id"], edge["from_node_id"])
        new_edge["to_node_id"] = mapping.get(edge["to_node_id"], edge["to_node_id"])
        namespaced_edges.append(new_edge)
    return {**graph, "nodes": namespaced_nodes, "edges": namespaced_edges}


def _build_model_resolver(session):
    from app.config import get_settings
    from app.credentials import crypto
    from app.credentials.repository import CredentialRepository
    from app.credentials.vault import CredentialVault
    from app.extraction.model_resolver import ExtractionModelResolver
    from app.providers.repository import ModelConfigRepository, SearchConfigRepository
    from app.providers.service import ProviderService

    settings = get_settings()
    vault = CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(session),
    )
    provider_service = ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(session),
        search_configs=SearchConfigRepository(session),
    )
    resolver = ExtractionModelResolver(session, provider_service=provider_service, vault=vault)
    return resolver, provider_service


def _previous_frozen_search_config(
    session,
    *,
    user_id: int,
    task_id: int,
    spec_version: int,
    before_plan_version: int,
) -> tuple[str | None, int | None]:
    """Latest prior READY preflight's frozen (search_config_id, version), if any.

    A continuation plan version inherits the Run's frozen SearchConfig instead of
    re-selecting the current default provider. Without this, Round-2 source_search
    looks up a READY preflight for the new plan_version, finds none, and fails with
    ``FROZEN_CONFIG_UNAVAILABLE`` even though the frozen config is still valid.
    """
    from sqlalchemy import select

    from app.domain.models import ExecutionPreflightResult

    row = session.scalar(
        select(ExecutionPreflightResult)
        .where(
            ExecutionPreflightResult.user_id == user_id,
            ExecutionPreflightResult.task_id == task_id,
            ExecutionPreflightResult.spec_version == spec_version,
            ExecutionPreflightResult.plan_version < before_plan_version,
            ExecutionPreflightResult.status == "READY",
        )
        .order_by(ExecutionPreflightResult.plan_version.desc(), ExecutionPreflightResult.id.desc())
        .limit(1)
    )
    if row is None or row.search_config_id is None or row.search_config_version is None:
        return None, None
    return row.search_config_id, row.search_config_version


def _ensure_continuation_preflight(
    session,
    *,
    user_id: int,
    task_id: int,
    spec_version: int,
    new_plan_version: int,
) -> bool:
    """Persist a preflight for ``new_plan_version`` carrying the Run's frozen SearchConfig.

    Returns True when the preflight is READY. Returns False when it is BLOCKED — the caller
    surfaces that as an explicit replan failure instead of silently switching to the current
    default provider or returning empty results.
    """
    from app.config import get_settings
    from app.plan.preflight import ExecutionPreflightService, ExecutionPreflightStatus

    # autoflush=False：让 pending 的 PlanVersion V2 / task.current_plan_version 对
    # 后续 preflight 查询可见（_context_matches 必须读到 current_plan_version == 新版本）。
    session.flush()

    frozen_id, frozen_version = _previous_frozen_search_config(
        session,
        user_id=user_id,
        task_id=task_id,
        spec_version=spec_version,
        before_plan_version=new_plan_version,
    )
    outcome = ExecutionPreflightService(session, settings=get_settings()).evaluate(
        user_id=user_id,
        task_id=task_id,
        spec_version=spec_version,
        plan_version=new_plan_version,
        frozen_search_config_id=frozen_id,
        frozen_search_config_version=frozen_version,
    )
    return outcome.status is ExecutionPreflightStatus.READY


@activity.defn
async def replan_for_continuation(
    inp: ReplanContinuationInput,
) -> ReplanContinuationResult:
    session = get_session_factory()()
    try:
        from sqlalchemy import select

        from app.auth.models import User
        from app.domain.models import Run
        from app.domain.repository import (
            PlanVersionRepository,
            SpecVersionRepository,
            TaskRepository,
        )
        from app.domain.task_types import TaskType
        from app.plan.diff import PlanDiff
        from app.plan.nodes import NodeRegistry
        from app.plan.schemas import PlanGraphDraft, PlanValidationResult
        from app.plan.service import plan_fingerprint
        from app.plan.validator import validate_plan
        from app.state.events import append_domain_event, enqueue_outbox

        run = session.scalar(select(Run).where(Run.id == inp.run_id, Run.user_id == inp.user_id))
        if run is None or run.task_id != inp.task_id or run.spec_version != inp.spec_version:
            return ReplanContinuationResult(status="FAILED", failure_code="RUN_NOT_FOUND")
        spec = SpecVersionRepository(session).get_version(
            inp.user_id, inp.task_id, inp.spec_version
        )
        owner = session.get(User, inp.user_id)
        if owner is None:
            return ReplanContinuationResult(status="FAILED", failure_code="OWNER_NOT_FOUND")

        resolver, provider_service = _build_model_resolver(session)
        resolved, api_key, _audit = resolver.resolve_for_run(run)
        if resolved is None or api_key is None:
            return ReplanContinuationResult(status="FAILED", failure_code="MODEL_UNAVAILABLE")

        from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
        from app.config import get_settings

        spec_payload = spec.payload or {}
        task_type = TaskType(spec_payload.get("task_type") or "SPECIFIED_SOURCE")
        has_search = bool(provider_service.list_search_configs(owner))
        continuation_context = {
            "instruction": (
                "上一轮采集结果不足（未达到最低合格记录或未覆盖目标来源），需要受控继续发现来源。"
                "生成新的执行计划，不得扩大 Spec 范围、不得改变字段含义、不得降低质量要求。"
            ),
            "continue_hints": inp.continue_hints,
            "search_round_count": inp.search_round_count,
        }
        plan_input = PlanInput(
            task_id=inp.task_id,
            spec_version=inp.spec_version,
            spec_payload=spec_payload,
            task_type=task_type,
            registry_metadata=NodeRegistry().planning_metadata(),
            execution_constraints={"has_search_provider": has_search},
            repair_context=continuation_context,
        )
        agent = PlanGeneratorAgent(settings=get_settings())
        graph = await agent.generate(plan_input, resolved, api_key)

        registry = NodeRegistry()
        outcome = validate_plan(
            graph,
            spec_payload,
            registry,
            available_search=has_search,
            spec_version=inp.spec_version,
        )
        if outcome.result not in (
            PlanValidationResult.VALID,
            PlanValidationResult.REQUIRES_APPROVAL,
        ):
            return ReplanContinuationResult(
                status="FAILED",
                failure_code=f"REPLAN_INVALID:{outcome.result.value}",
            )

        repo = PlanVersionRepository(session)
        parent = repo.get_version(inp.user_id, inp.task_id, inp.current_plan_version)
        if parent is None:
            return ReplanContinuationResult(status="FAILED", failure_code="PARENT_PLAN_NOT_FOUND")
        # 幂等：一个父 PlanVersion 至多一个 continuation 子版本。Worker 崩溃/Temporal 重试
        # 后重放时复用已创建的 vN+1，不重复生成（D-016）。
        from app.domain.models import PlanVersion

        existing_child = session.scalar(
            select(PlanVersion).where(
                PlanVersion.user_id == inp.user_id,
                PlanVersion.task_id == inp.task_id,
                PlanVersion.parent_plan_version_id == parent.id,
            )
        )
        if existing_child is not None:
            run.plan_version = existing_child.version
            session.add(run)
            task_row = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
            task_row.current_plan_version = existing_child.version
            session.add(task_row)
            if not _ensure_continuation_preflight(
                session,
                user_id=inp.user_id,
                task_id=inp.task_id,
                spec_version=inp.spec_version,
                new_plan_version=existing_child.version,
            ):
                return ReplanContinuationResult(
                    status="FAILED", failure_code="FROZEN_CONFIG_UNAVAILABLE"
                )
            session.commit()
            return ReplanContinuationResult(new_plan_version=existing_child.version, status="OK")
        new_version = parent.version + 1
        registry_versions = {d.node_type.value: d.definition_version for d in registry.all()}
        # Diff 在命名空间化之前计算（旧/新 node_id 语义一致，diff 才有意义）。
        parent_graph = ((parent.payload or {}).get("graph") or {}) if parent.payload else {}
        diff_summary = None
        if parent_graph.get("nodes"):
            diff_summary = PlanDiff.compute(
                PlanGraphDraft.model_validate(parent_graph), graph
            ).model_dump(mode="json")
        namespaced_graph = _namespace_graph(graph.model_dump(mode="json"), new_version)

        repo.create(
            user_id=inp.user_id,
            task_id=inp.task_id,
            spec_version=inp.spec_version,
            version=new_version,
            payload={"graph": namespaced_graph, "validator_issues": []},
            parent_plan_version_id=parent.id,
            validation_status=outcome.result.value,
            plan_fingerprint=plan_fingerprint(namespaced_graph, registry_versions),
            registry_versions=registry_versions,
            generation_policy="replan",
            trigger_reason="continuation_search_more_required",
            replan_evidence_refs=[inp.continue_hints],
            diff_summary=diff_summary,
            commit=False,
        )
        run.plan_version = new_version
        session.add(run)
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        task.current_plan_version = new_version
        task.version += 1
        session.add(task)
        append_domain_event(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type="task.plan_replanned",
            aggregate_version=task.version,
            payload={
                "plan_version": new_version,
                "search_round_count": inp.search_round_count,
                "trigger_reason": "continuation_search_more_required",
            },
            actor_type="system",
            run_id=inp.run_id,
        )
        enqueue_outbox(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type="task.plan_replanned",
            payload={
                "plan_version": new_version,
                "search_round_count": inp.search_round_count,
                "trigger_reason": "continuation_search_more_required",
            },
            dispatch_key=f"task:{inp.task_id}:plan_replanned",
        )
        if not _ensure_continuation_preflight(
            session,
            user_id=inp.user_id,
            task_id=inp.task_id,
            spec_version=inp.spec_version,
            new_plan_version=new_version,
        ):
            return ReplanContinuationResult(
                status="FAILED", failure_code="FROZEN_CONFIG_UNAVAILABLE"
            )
        session.commit()
        return ReplanContinuationResult(new_plan_version=new_version, status="OK")
    except Exception:
        session.rollback()
        return ReplanContinuationResult(status="FAILED", failure_code="REPLAN_INTERNAL")
    finally:
        session.close()


__all__ = [
    "ReplanContinuationInput",
    "ReplanContinuationResult",
    "replan_for_continuation",
]
