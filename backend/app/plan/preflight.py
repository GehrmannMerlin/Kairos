"""Deterministic, side-effect-free execution readiness evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.errors import NotFoundError
from app.config import Settings
from app.credentials.models import ModelConfig, SearchConfig
from app.domain.models import CollectionSpecVersion, PlanVersion, Task
from app.domain.spec import validate_confirmable_spec_payload
from app.domain.task_types import TaskType
from app.plan.capabilities import (
    CAPABILITY_MANIFEST_VERSION,
    PRODUCTION_EXECUTOR_CAPABILITIES,
    supported_node_types,
)
from app.plan.nodes import NodeType
from app.plan.preflight_repository import ExecutionPreflightRepository
from app.providers.repository import SearchConfigRepository
from app.reliability.pools import WorkerRole, parse_worker_roles


class ExecutionPreflightStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class PreflightIssue(BaseModel):
    code: str
    safe_message: str
    remediation: str
    node_id: str | None = None
    field: str | None = None


class ExecutionPreflightOutcome(BaseModel):
    result_id: int = 0
    created: bool = False
    status: ExecutionPreflightStatus
    task_id: int
    spec_version: int
    plan_version: int
    capability_manifest_version: str
    issues: list[PreflightIssue]
    search_config_id: str | None = None
    search_config_version: int | None = None

    @property
    def issue_codes(self) -> Sequence[str]:
        return tuple(sorted(issue.code for issue in self.issues))


class ExecutionPreflightService:
    """Evaluate frozen execution inputs without providers, secrets, or network I/O."""

    def __init__(
        self,
        db: DbSession,
        *,
        settings: Settings,
        supported_nodes: set[NodeType] | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._supported_nodes = (
            supported_nodes if supported_nodes is not None else supported_node_types()
        )
        self._search_configs = SearchConfigRepository(db)
        self._repository = ExecutionPreflightRepository(db)

    def evaluate(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int
    ) -> ExecutionPreflightOutcome:
        task, spec, plan = self._load_owned_frozen_inputs(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
        )
        issues: list[PreflightIssue] = []
        graph = self._graph(plan)

        if not self._context_matches(task, spec, plan, graph):
            issues.append(
                self._issue(
                    "PLAN_CONTEXT_MISMATCH",
                    "冻结的 Plan、Spec 与任务上下文不一致。",
                    "请重新生成与当前冻结 Spec 对应的 Plan。",
                )
            )

        if not self._has_materializable_input(spec):
            issues.append(
                self._issue(
                    "EXECUTION_INPUT_UNMATERIALIZABLE",
                    "冻结的来源输入无法直接执行。",
                    "请提供可执行的完整来源网址后重新确认 Spec。",
                    field="source_scope.seed_urls",
                )
            )

        graph_nodes = graph.get("nodes")
        nodes: list = graph_nodes if isinstance(graph_nodes, list) else []
        needs_search = any(
            isinstance(node, dict) and node.get("node_type") == NodeType.SOURCE_SEARCH.value
            for node in nodes
        )
        search = self._available_search_config(user_id) if needs_search else None
        if needs_search and search is None:
            issues.append(
                self._issue(
                    "FROZEN_CONFIG_UNAVAILABLE",
                    "执行所需的搜索服务配置不可用。",
                    "请配置并测试可用的搜索服务后重新执行检查。",
                )
            )

        if not self._frozen_model_config_available(user_id, plan):
            issues.append(
                self._issue(
                    "FROZEN_CONFIG_UNAVAILABLE",
                    "冻结的模型配置不可用。",
                    "请恢复该模型配置或重新生成 Plan。",
                )
            )

        self._check_capabilities_and_routes(nodes, issues)

        if not self._artifact_storage_configured():
            issues.append(
                self._issue(
                    "ARTIFACT_STORAGE_UNAVAILABLE",
                    "产物存储配置不可用。",
                    "请配置对象存储端点、Bucket 和访问凭据后重试。",
                )
            )

        outcome = ExecutionPreflightOutcome(
            status=ExecutionPreflightStatus.BLOCKED if issues else ExecutionPreflightStatus.READY,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            capability_manifest_version=CAPABILITY_MANIFEST_VERSION,
            issues=issues,
            search_config_id=search.config_id if search is not None else None,
            search_config_version=search.version if search is not None else None,
        )
        row, created = self._repository.get_or_create(outcome)
        return outcome.model_copy(update={"result_id": row.id, "created": created})

    def _load_owned_frozen_inputs(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int
    ) -> tuple[Task, CollectionSpecVersion, PlanVersion]:
        task = self._db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
        if task is None:
            raise NotFoundError("资源不存在")
        spec = self._db.scalar(
            select(CollectionSpecVersion).where(
                CollectionSpecVersion.task_id == task_id,
                CollectionSpecVersion.user_id == user_id,
                CollectionSpecVersion.version == spec_version,
            )
        )
        plan = self._db.scalar(
            select(PlanVersion).where(
                PlanVersion.task_id == task_id,
                PlanVersion.user_id == user_id,
                PlanVersion.version == plan_version,
            )
        )
        if spec is None or plan is None:
            raise NotFoundError("资源不存在")
        return task, spec, plan

    @staticmethod
    def _graph(plan: PlanVersion) -> dict:
        payload = plan.payload if isinstance(plan.payload, dict) else {}
        graph = payload.get("graph")
        return graph if isinstance(graph, dict) else {}

    @staticmethod
    def _context_matches(
        task: Task, spec: CollectionSpecVersion, plan: PlanVersion, graph: dict
    ) -> bool:
        task_type = spec.payload.get("task_type") if isinstance(spec.payload, dict) else None
        return (
            task.current_spec_version == spec.version
            and task.current_plan_version == plan.version
            and plan.spec_version == spec.version
            and graph.get("task_id") == task.id
            and graph.get("spec_version") == spec.version
            and graph.get("task_type") == task_type
        )

    @staticmethod
    def _has_materializable_input(spec: CollectionSpecVersion) -> bool:
        try:
            validated = validate_confirmable_spec_payload(spec.payload)
        except Exception:
            return False
        return not (
            validated.task_type is TaskType.SPECIFIED_SOURCE
            and not validated.source_scope.seed_urls
        )

    def _available_search_config(self, user_id: int) -> SearchConfig | None:
        return self._search_configs.get_first_available(user_id)

    def _frozen_model_config_available(self, user_id: int, plan: PlanVersion) -> bool:
        if plan.model_config_id is None or plan.model_config_version is None:
            return True
        row = self._db.scalar(
            select(ModelConfig).where(
                ModelConfig.user_id == user_id,
                ModelConfig.config_id == plan.model_config_id,
                ModelConfig.version == plan.model_config_version,
                ModelConfig.connection_status == "available",
            )
        )
        return row is not None

    def _check_capabilities_and_routes(self, nodes: list, issues: list[PreflightIssue]) -> None:
        capabilities = {
            capability.node_type: capability for capability in PRODUCTION_EXECUTOR_CAPABILITIES
        }
        configured_roles = self._configured_worker_roles()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("node_id") if isinstance(node.get("node_id"), str) else None
            try:
                node_type = NodeType(str(node.get("node_type")))
            except (TypeError, ValueError):
                issues.append(
                    self._issue(
                        "EXECUTION_CAPABILITY_UNAVAILABLE",
                        "Plan 包含当前生产执行器不支持的节点。",
                        "请重新生成仅使用已部署执行能力的 Plan。",
                        node_id=node_id,
                    )
                )
                continue
            capability = capabilities.get(node_type)
            if node_type not in self._supported_nodes or capability is None:
                issues.append(
                    self._issue(
                        "EXECUTION_CAPABILITY_UNAVAILABLE",
                        "Plan 包含当前生产执行器不支持的节点。",
                        "请重新生成仅使用已部署执行能力的 Plan。",
                        node_id=node_id,
                    )
                )
                continue
            if not self._queue_available(capability.task_queue_role, configured_roles):
                issues.append(
                    self._issue(
                        "TASK_QUEUE_ROUTE_UNAVAILABLE",
                        "节点所需的执行队列当前不可用。",
                        "请启用对应 Worker 队列后重新执行检查。",
                        node_id=node_id,
                    )
                )

    def _configured_worker_roles(self) -> set[WorkerRole]:
        try:
            return set(parse_worker_roles(self._settings.worker_roles))
        except ValueError:
            return set()

    def _queue_available(self, role: str, configured_roles: set[WorkerRole]) -> bool:
        if not self._settings.temporal_task_queue.strip() and role == WorkerRole.CORE.value:
            return False
        return WorkerRole.ALL in configured_roles or WorkerRole(role) in configured_roles

    def _artifact_storage_configured(self) -> bool:
        return all(
            value.strip()
            for value in (
                self._settings.s3_endpoint,
                self._settings.s3_bucket,
                self._settings.s3_access_key,
            )
        )

    @staticmethod
    def _issue(
        code: str,
        safe_message: str,
        remediation: str,
        *,
        node_id: str | None = None,
        field: str | None = None,
    ) -> PreflightIssue:
        return PreflightIssue(
            code=code,
            safe_message=safe_message,
            remediation=remediation,
            node_id=node_id,
            field=field,
        )
