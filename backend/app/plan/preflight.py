"""Deterministic, side-effect-free execution readiness evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import event, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import SessionTransaction

from app.auth.errors import NotFoundError
from app.config import Settings
from app.credentials.models import ModelConfig, SearchConfig
from app.domain.models import CollectionSpecVersion, ExecutionPreflightResult, PlanVersion, Task
from app.domain.spec import validate_confirmable_spec_payload
from app.domain.task_types import TaskType
from app.observability.execution_metrics import get_execution_metrics
from app.plan.capabilities import (
    CAPABILITY_MANIFEST_VERSION,
    PRODUCTION_EXECUTOR_CAPABILITIES,
    supported_node_types,
)
from app.plan.nodes import NodeType
from app.plan.preflight_repository import ExecutionPreflightRepository
from app.providers.repository import SearchConfigRepository
from app.reliability.pools import WorkerRole, parse_worker_roles

_PENDING_METRICS_KEY = "execution_preflight_pending_metrics"
_METRIC_LISTENERS_KEY = "execution_preflight_metric_listeners"


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
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        plan_version: int,
        frozen_search_config_id: str | None = None,
        frozen_search_config_version: int | None = None,
    ) -> ExecutionPreflightOutcome:
        """Persist a READY/BLOCKED readiness fact.

        ``frozen_search_config_id/version`` — when given, resolve exactly that immutable
        SearchConfig row instead of the current default. Used by replan to carry the Run's
        frozen config into continuation plan versions (Round-2 continuation must not silently
        switch to a newer default provider). A missing frozen row blocks without falling back.
        """
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
        search = None
        if needs_search:
            frozen_id, frozen_version = frozen_search_config_id, frozen_search_config_version
            if frozen_id is None and frozen_version is None:
                search = self._available_search_config(user_id)
                if search is None:
                    issues.append(
                        self._issue(
                            "FROZEN_CONFIG_UNAVAILABLE",
                            "执行所需的搜索服务配置不可用。",
                            "请配置并测试可用的搜索服务后重新执行检查。",
                        )
                    )
            elif frozen_id is None or frozen_version is None:
                issues.append(
                    self._issue(
                        "FROZEN_CONFIG_UNAVAILABLE",
                        "冻结的搜索服务配置标识不完整。",
                        "请恢复该搜索服务配置后重新执行检查。",
                    )
                )
            else:
                try:
                    search = self._search_configs.get_version(user_id, frozen_id, frozen_version)
                except NotFoundError:
                    issues.append(
                        self._issue(
                            "FROZEN_CONFIG_UNAVAILABLE",
                            "冻结的搜索服务配置已不可用。",
                            "请恢复该搜索服务配置后重新执行检查。",
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
        persisted = self._outcome_from_persisted_result(row, created=created)
        if created:
            _record_preflight_after_commit(self._db, persisted)
        return persisted

    @staticmethod
    def _outcome_from_persisted_result(
        row: ExecutionPreflightResult, *, created: bool
    ) -> ExecutionPreflightOutcome:
        """Return the immutable persisted fact, never a newly computed candidate."""
        return ExecutionPreflightOutcome(
            result_id=row.id,
            created=created,
            status=ExecutionPreflightStatus(row.status),
            task_id=row.task_id,
            spec_version=row.spec_version,
            plan_version=row.plan_version,
            capability_manifest_version=row.capability_manifest_version,
            issues=[PreflightIssue.model_validate(issue) for issue in row.issues],
            search_config_id=row.search_config_id,
            search_config_version=row.search_config_version,
        )

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
        if plan.model_config_id is None and plan.model_config_version is None:
            return True
        if plan.model_config_id is None or plan.model_config_version is None:
            return False
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


def _record_preflight_after_commit(db: DbSession, outcome: ExecutionPreflightOutcome) -> None:
    """Defer the metric until the caller's owning transaction is durable."""
    if not db.info.get(_METRIC_LISTENERS_KEY):
        event.listen(db, "after_commit", _flush_committed_preflight_metrics)
        event.listen(db, "after_rollback", _discard_rolled_back_preflight_metrics)
        event.listen(db, "after_soft_rollback", _discard_soft_rolled_back_preflight_metrics)
        event.listen(db, "after_transaction_end", _discard_closed_transaction_metrics)
        db.info[_METRIC_LISTENERS_KEY] = True
    owner = db.get_nested_transaction() or db.get_transaction()
    if owner is None:
        raise RuntimeError("preflight metric scheduled without an owning transaction")
    pending = db.info.setdefault(_PENDING_METRICS_KEY, [])
    pending.append((owner, outcome.status.value, tuple(outcome.issue_codes)))


def _flush_committed_preflight_metrics(db: DbSession) -> None:
    # A repository savepoint also emits after_commit; only the outer transaction
    # makes the preflight fact durable.
    if db.in_nested_transaction():
        return
    pending = db.info.pop(_PENDING_METRICS_KEY, [])
    metrics = get_execution_metrics()
    for _owner, status, issue_codes in pending:
        metrics.record_preflight(status=status, issue_codes=issue_codes)


def _discard_rolled_back_preflight_metrics(db: DbSession) -> None:
    if not db.in_nested_transaction():
        db.info.pop(_PENDING_METRICS_KEY, None)


def _discard_soft_rolled_back_preflight_metrics(
    db: DbSession, transaction: SessionTransaction
) -> None:
    pending = db.info.get(_PENDING_METRICS_KEY, [])
    db.info[_PENDING_METRICS_KEY] = [
        entry for entry in pending if not _transaction_descends_from(entry[0], transaction)
    ]


def _discard_closed_transaction_metrics(db: DbSession, transaction: SessionTransaction) -> None:
    # Session.close() ends an outer transaction without after_rollback.
    if transaction.parent is None:
        db.info.pop(_PENDING_METRICS_KEY, None)


def _transaction_descends_from(owner: SessionTransaction, ancestor: SessionTransaction) -> bool:
    current: SessionTransaction | None = owner
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False
