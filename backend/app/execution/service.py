"""M-14 Execution read-model service（D-055/D-063）。

阶段聚合来自 Run/DomainEvent/URLResource/Record 真实事实；NodeRun/NodeAttempt
在当前采集执行路径不产生行，因此不伪造 Node 级数据。Timeline 只暴露 allowlist
字段，payload 中的 secret 绝不进入响应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.auth.errors import NotFoundError
from app.execution.contracts import (
    DagEdge,
    DagNodeDto,
    DagNodeExecution,
    DagView,
    ExecutionCounts,
    ExecutionNodeSummary,
    ExecutionView,
    NodeDetailDto,
    PlanBrief,
    RunSummary,
    StageKey,
    StageState,
    StageSummary,
    TimelineCategory,
    TimelineEvent,
    TimelinePage,
)
from app.execution.repository import ExecutionRepository

_SECRET_KEYS = {
    "credential_ref",
    "password",
    "api_key",
    "cookie",
    "authorization",
    "proxy_authorization",
    "token",
    "secret",
    "session_token",
}

_STAGE_LABELS = {
    StageKey.GOAL_PLAN: "目标与计划",
    StageKey.SOURCE_DISCOVERY: "来源发现",
    StageKey.FETCH: "网页抓取",
    StageKey.EXTRACTION: "字段提取",
    StageKey.VALIDATION: "数据验证",
}
_STAGE_ORDER = [
    StageKey.GOAL_PLAN,
    StageKey.SOURCE_DISCOVERY,
    StageKey.FETCH,
    StageKey.EXTRACTION,
    StageKey.VALIDATION,
]

_NODE_TYPE_STAGE: dict[str, StageKey] = {
    "source_search": StageKey.SOURCE_DISCOVERY,
    "access_rules_check": StageKey.SOURCE_DISCOVERY,
    "link_discovery": StageKey.SOURCE_DISCOVERY,
    "fetch": StageKey.FETCH,
    "browser_render": StageKey.FETCH,
    "extract": StageKey.EXTRACTION,
    "normalize": StageKey.EXTRACTION,
    "deduplicate": StageKey.VALIDATION,
    "validate": StageKey.VALIDATION,
    "generate_artifact": StageKey.VALIDATION,
}

_ERROR_TYPES = {
    "task.fail",
    "fetch.failed",
    "extraction.failed",
    "node.blocked_high_risk",
    "run.node_failed",
    "run.failed",
}
_TOOL_UPGRADE_TYPES = {
    "fetch.escalated",
    "fetch.strategy_selected",
    "extraction.llm_fallback_used",
    "extraction.rule_promoted",
    "discovery.access_checked",
}
_PLAN_CHANGE_TYPES = {"task.plan_generated", "task.plan_replanned", "task.spec_confirmed"}
_PAUSE_RESUME_TYPES = {
    "task.pause",
    "task.mark_paused",
    "task.resume",
    "task.cancel",
    "task.mark_cancelled",
}

_TASK_EVENT_LABELS = {
    "task.submit": "任务已提交",
    "task.start": "开始执行",
    "task.pause": "请求暂停",
    "task.mark_paused": "已暂停",
    "task.resume": "已恢复",
    "task.cancel": "请求取消",
    "task.mark_cancelled": "已取消",
    "task.complete": "任务已完成",
    "task.mark_partial": "部分完成",
    "task.fail": "任务执行失败",
    "task.mark_waiting_approval": "等待审批",
    "task.mark_waiting_resource": "等待执行资源",
    "task.resource_waiting": "等待可用执行资源",
    "task.delete": "任务已删除",
    "task.restore": "任务已恢复",
    "task.spec_confirmed": "采集方案已确认",
}
_DISCOVERY_LABELS = {
    "discovery.candidates_found": "发现候选来源",
    "discovery.expanded": "站内链接扩展",
    "discovery.approval_required": "来源发现需要审批",
    "discovery.access_checked": "访问规则检查",
}
_RECORD_LABELS = {
    "record.approved": "记录已通过",
    "record.rejected": "记录已拒绝",
    "record.edited": "记录已人工修正",
    "record.reevaluate_requested": "记录重新处理请求",
    "record.approved_batch": "批量通过",
    "record.rejected_batch": "批量拒绝",
}
_NODE_RESOURCE_LABELS = {
    "node.resource_waiting": "等待执行资源",
}
_RUN_EVENT_LABELS = {
    "run.started": "执行已开始",
    "run.node_started": "节点已开始",
    "run.node_progress": "节点进度已更新",
    "run.checkpoint_committed": "检查点已提交",
    "run.node_completed": "节点已完成",
    "run.node_blocked": "节点已阻塞",
    "run.node_failed": "节点执行失败",
    "run.completed": "执行已完成",
    "run.partially_completed": "执行部分完成",
    "run.failed": "执行失败",
    "run.cancelled": "执行已取消",
}
_CURRENT_NODE_STATES = frozenset({"RUNNING", "WAITING_RESOURCE", "BLOCKED"})
_SUCCESSFUL_NODE_STATES = frozenset({"SUCCEEDED"})
_RUN_TERMINAL_TYPES = frozenset(
    {
        "run.completed",
        "run.partially_completed",
        "run.failed",
        "run.cancelled",
    }
)

_BATCH_SIZE = 500


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


@dataclass
class _NodeEventFacts:
    event_count: int = 0
    last_status: str | None = None
    last_error: str | None = None
    attempt_count: int = 0
    tool: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


@dataclass
class _EventFacts:
    last_event_id: int = 0
    last_activity_at: datetime | None = None
    stage_event_counts: dict[StageKey, int] = field(
        default_factory=lambda: dict.fromkeys(_STAGE_ORDER, 0)
    )
    stage_error_counts: dict[StageKey, int] = field(
        default_factory=lambda: dict.fromkeys(_STAGE_ORDER, 0)
    )
    outcome_code: str | None = None
    waiting_reason_code: str | None = None
    url_states: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, _NodeEventFacts] = field(default_factory=dict)


class ExecutionService:
    def __init__(self, db: Any) -> None:
        self._repo = ExecutionRepository(db)

    # ---- overview ----
    def assemble_overview(self, *, user_id: int, task_id: int) -> ExecutionView:
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        plan = (
            self._repo.plan_version(
                user_id=user_id,
                task_id=task_id,
                version=run.plan_version,
            )
            if run is not None
            else self._repo.latest_plan(user_id=user_id, task_id=task_id)
        )
        urls = self._repo.url_stats(user_id=user_id, task_id=task_id)
        records = self._repo.record_counts(user_id=user_id, task_id=task_id)
        run_record_total = self._run_record_count(user_id=user_id, task_id=task_id, run=run)
        run_validated_record_count = self._run_validated_record_count(
            user_id=user_id, task_id=task_id, run=run
        )

        node_facts = self._node_facts(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id if run is not None else None,
        )
        labels = self._node_labels(plan)
        current_fact = self._latest_node_fact(
            [fact for fact in node_facts if fact[0].state in _CURRENT_NODE_STATES]
        )
        successful_fact = self._latest_node_fact(
            [fact for fact in node_facts if fact[0].state in _SUCCESSFUL_NODE_STATES]
        )
        event_facts = self._event_facts(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id if run is not None else None,
        )
        run_urls = self._run_url_stats(
            user_id=user_id,
            task_id=task_id,
            run=run,
            event_url_states=event_facts.url_states,
        )
        waiting_reason_code = self._waiting_reason(
            current_fact,
            event_facts.waiting_reason_code,
        )
        legacy_execution_facts = run is not None and not node_facts
        stage_urls = urls if legacy_execution_facts else run_urls
        stage_record_total = sum(records.values()) if legacy_execution_facts else run_record_total
        run_state = run.state if run else None
        stages: list[StageSummary] = []
        for key in _STAGE_ORDER:
            state = self._stage_state_from_facts(
                key=key,
                stage_event_counts=event_facts.stage_event_counts,
                run_state=run_state,
            )
            url_processed = (
                stage_urls["fetched"] + stage_urls["failed"]
                if key == StageKey.FETCH
                else (stage_urls["discovered"] if key == StageKey.SOURCE_DISCOVERY else 0)
            )
            record_count = (
                stage_record_total if key in (StageKey.EXTRACTION, StageKey.VALIDATION) else 0
            )
            stages.append(
                StageSummary(
                    key=key,
                    label=_STAGE_LABELS[key],
                    state=state,
                    event_count=event_facts.stage_event_counts[key],
                    url_processed=url_processed,
                    record_count=record_count,
                    error_count=event_facts.stage_error_counts[key],
                )
            )
        return ExecutionView(
            task_id=task_id,
            run=(
                RunSummary(
                    run_id=run.id,
                    state=run.state,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    plan_version=run.plan_version,
                    spec_version=run.spec_version,
                )
                if run
                else None
            ),
            stages=stages,
            urls=urls,
            records=records,
            plan=(
                PlanBrief(
                    plan_version=plan.version,
                    node_count=len(((plan.payload or {}).get("graph") or {}).get("nodes") or []),
                    validation_status=plan.validation_status,
                )
                if plan
                else None
            ),
            current_node=self._node_summary(current_fact, labels),
            last_successful_node=self._node_summary(successful_fact, labels),
            last_activity_at=self._last_activity(node_facts, event_facts.last_activity_at),
            last_event_id=event_facts.last_event_id,
            counts=ExecutionCounts(
                discovered_pages=run_urls.get("discovered", 0),
                fetched_pages=run_urls.get("fetched", 0),
                extracted_records=run_record_total,
                validated_records=run_validated_record_count,
            ),
            waiting_reason_code=waiting_reason_code,
            outcome_code=event_facts.outcome_code,
            legacy_execution_facts=legacy_execution_facts,
        )

    def _event_facts(self, *, user_id: int, task_id: int, run_id: int | None) -> _EventFacts:
        """Reduce a frozen event snapshot without retaining processed pages."""
        cursor = 0
        through_id = self._repo.max_event_id(user_id=user_id, task_id=task_id)
        facts = _EventFacts(last_event_id=through_id)
        while cursor < through_id:
            page = self._repo.events_after(
                user_id=user_id,
                task_id=task_id,
                after_id=cursor,
                limit=_BATCH_SIZE,
                through_id=through_id,
            )
            if not page:
                return facts
            next_cursor = page[-1].id
            if next_cursor <= cursor:
                raise RuntimeError("execution event page did not advance cursor")
            for event in page:
                if run_id is None or event.run_id == run_id:
                    self._accumulate_event(facts, event)
            cursor = next_cursor
        return facts

    def _accumulate_event(self, facts: _EventFacts, event: Any) -> None:
        payload = event.payload or {}
        stage = self._stage(event)
        facts.stage_event_counts[stage] += 1
        if "error" in self._classify(event):
            facts.stage_error_counts[stage] += 1
        facts.last_activity_at = self._max_datetime([facts.last_activity_at, event.occurred_at])

        if event.event_type in _RUN_TERMINAL_TYPES:
            value = payload.get("outcome_code") or payload.get("error_code")
            facts.outcome_code = str(value) if value else None
        if event.event_type in {"run.node_blocked", "node.resource_waiting"}:
            value = payload.get("reason_code") or payload.get("waiting_reason_code")
            if value:
                facts.waiting_reason_code = str(value)

        raw_url_hash = payload.get("url_hash")
        if raw_url_hash:
            facts.url_states.setdefault(str(raw_url_hash), "DISCOVERED")
        if event.event_type in {"fetch.completed", "fetch.failed"}:
            url_ref = str(raw_url_hash or f"event:{event.id}")
            facts.url_states[url_ref] = (
                "FETCHED" if event.event_type == "fetch.completed" else "FAILED"
            )

        node_id = payload.get("node_id")
        if node_id is None:
            return
        node = facts.nodes.setdefault(str(node_id), _NodeEventFacts())
        node.event_count += 1
        if payload.get("status") is not None or payload.get("state") is not None:
            node.last_status = str(payload.get("status") or payload.get("state"))
        if payload.get("error_code") or payload.get("reason_code"):
            node.last_error = str(payload.get("error_code") or payload.get("reason_code"))
        node.attempt_count = max(node.attempt_count, _safe_int(payload.get("attempt")))
        if payload.get("tool"):
            node.tool = str(payload["tool"])
        if payload.get("model"):
            node.model = str(payload["model"])
        if payload.get("duration_ms") is not None:
            node.duration_ms = _safe_int(payload["duration_ms"])
        if payload.get("tokens_in") is not None:
            node.tokens_in = _safe_int(payload["tokens_in"])
        if payload.get("tokens_out") is not None:
            node.tokens_out = _safe_int(payload["tokens_out"])

    def _run_url_stats(
        self,
        *,
        user_id: int,
        task_id: int,
        run: Any | None,
        event_url_states: dict[str, str],
    ) -> dict[str, int]:
        if run is None:
            return {"discovered": 0, "fetched": 0, "failed": 0, "pending": 0}
        states = self._repo.run_url_facts(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id,
            spec_version=run.spec_version,
        )
        states.update(event_url_states)
        fetched = sum(state in {"FETCHED", "HANDED_OFF"} for state in states.values())
        failed = sum(state == "FAILED" for state in states.values())
        discovered = len(states)
        return {
            "discovered": discovered,
            "fetched": fetched,
            "failed": failed,
            "pending": discovered - fetched - failed,
        }

    def _run_record_count(self, *, user_id: int, task_id: int, run: Any | None) -> int:
        if run is None:
            return 0
        return self._repo.run_record_count_total(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id,
            spec_version=run.spec_version,
        )

    def _run_validated_record_count(self, *, user_id: int, task_id: int, run: Any | None) -> int:
        if run is None:
            return 0
        return self._repo.run_validated_record_count(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id,
            spec_version=run.spec_version,
        )

    def _node_facts(
        self, *, user_id: int, task_id: int, run_id: int | None
    ) -> list[tuple[Any, Any | None]]:
        if run_id is None:
            return []
        facts: list[tuple[Any, Any | None]] = []
        for node in self._repo.node_runs(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
        ):
            if not node.node_id:
                continue
            facts.append(
                (
                    node,
                    self._repo.latest_attempt(user_id=user_id, node_run_id=node.id),
                )
            )
        return facts

    @staticmethod
    def _node_labels(plan: Any | None) -> dict[str, str]:
        graph = ((plan.payload or {}).get("graph") or {}) if plan is not None else {}
        labels: dict[str, str] = {}
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict) or not node.get("node_id"):
                continue
            node_id = str(node["node_id"])
            labels[node_id] = str(node.get("label") or node.get("name") or node_id)
        return labels

    def _latest_node_fact(
        self, facts: list[tuple[Any, Any | None]]
    ) -> tuple[Any, Any | None] | None:
        if not facts:
            return None
        return max(
            facts,
            key=lambda fact: (
                self._datetime_order(self._node_fact_activity(fact)),
                fact[0].position if fact[0].position is not None else -1,
                fact[0].id,
            ),
        )

    @staticmethod
    def _node_summary(
        fact: tuple[Any, Any | None] | None, labels: dict[str, str]
    ) -> ExecutionNodeSummary | None:
        if fact is None:
            return None
        node, attempt = fact
        return ExecutionNodeSummary(
            node_id=node.node_id,
            node_type=node.node_type,
            label=labels.get(node.node_id, node.node_id),
            state=node.state,
            attempt=attempt.attempt if attempt is not None else 0,
            safe_message=attempt.error_summary if attempt is not None else None,
        )

    @staticmethod
    def _node_fact_activity(fact: tuple[Any, Any | None]) -> datetime | None:
        node, attempt = fact
        candidates = [node.started_at, node.finished_at]
        if attempt is not None:
            candidates.extend([attempt.started_at, attempt.finished_at])
        return ExecutionService._max_datetime(candidates) or (
            attempt.created_at if attempt is not None else node.created_at
        )

    def _last_activity(
        self,
        node_facts: list[tuple[Any, Any | None]],
        event_activity_at: datetime | None,
    ) -> datetime | None:
        candidates = [self._node_fact_activity(fact) for fact in node_facts]
        candidates.append(event_activity_at)
        return self._max_datetime(candidates)

    @staticmethod
    def _max_datetime(values: list[datetime | None]) -> datetime | None:
        present = [value for value in values if value is not None]
        if not present:
            return None
        return max(present, key=ExecutionService._datetime_order)

    @staticmethod
    def _datetime_order(value: datetime | None) -> float:
        if value is None:
            return float("-inf")
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.timestamp()

    @staticmethod
    def _waiting_reason(
        current_fact: tuple[Any, Any | None] | None,
        event_waiting_reason_code: str | None,
    ) -> str | None:
        if current_fact is not None:
            node, attempt = current_fact
            if node.state in _CURRENT_NODE_STATES and attempt is not None and attempt.error_code:
                return str(attempt.error_code)
            return None
        return event_waiting_reason_code

    # ---- plan DAG + node detail ----
    def assemble_dag(self, *, user_id: int, task_id: int) -> DagView:
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        plan = (
            self._repo.plan_version(
                user_id=user_id,
                task_id=task_id,
                version=run.plan_version,
            )
            if run is not None
            else self._repo.latest_plan(user_id=user_id, task_id=task_id)
        )
        if plan is None:
            return DagView(
                task_id=task_id,
                plan_version=0,
                spec_version=0,
                validation_status="none",
                stage_status={k.value: "not_started" for k in _STAGE_ORDER},
            )
        graph = (plan.payload or {}).get("graph") or {}
        raw_nodes = graph.get("nodes") or []
        raw_edges = graph.get("edges") or []
        event_facts = self._event_facts(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id if run is not None else None,
        )
        urls = self._run_url_stats(
            user_id=user_id,
            task_id=task_id,
            run=run,
            event_url_states=event_facts.url_states,
        )
        record_total = self._run_record_count(user_id=user_id, task_id=task_id, run=run)
        node_facts = {
            node.node_id: (node, attempt)
            for node, attempt in self._node_facts(
                user_id=user_id,
                task_id=task_id,
                run_id=run.id if run is not None else None,
            )
        }
        run_state = run.state if run else None
        stage_status: dict[str, str] = {
            key.value: self._stage_state_from_facts(
                key=key,
                stage_event_counts=event_facts.stage_event_counts,
                run_state=run_state,
            )
            for key in _STAGE_ORDER
        }

        nodes: list[DagNodeDto] = []
        for n in raw_nodes:
            node_type = str(n.get("node_type") or "")
            node_id = str(n.get("node_id") or "")
            nodes.append(
                DagNodeDto(
                    node_id=node_id,
                    node_type=node_type,
                    definition_version=str(n.get("definition_version") or ""),
                    resource_class=self._resource_class(node_type),
                    depends_on=[str(d) for d in (n.get("depends_on") or [])],
                    optional=bool(n.get("optional")),
                    fail_policy=str(n.get("fail_policy") or "block"),
                    stage=self._node_stage(node_type).value,
                    parameters_summary=self._parameters_summary(n.get("parameters") or {}),
                    execution=self._node_execution(
                        node_type=node_type,
                        event_facts=event_facts.nodes.get(node_id),
                        urls=urls,
                        record_total=record_total,
                        node_fact=node_facts.get(node_id),
                    ),
                )
            )
        edges = [
            DagEdge(
                from_node_id=str(e.get("from_node_id") or ""),
                to_node_id=str(e.get("to_node_id") or ""),
            )
            for e in raw_edges
        ]
        return DagView(
            task_id=task_id,
            plan_version=plan.version,
            spec_version=plan.spec_version,
            validation_status=plan.validation_status,
            stage_status=stage_status,
            nodes=nodes,
            edges=edges,
        )

    def node_detail(self, *, user_id: int, task_id: int, node_id: str) -> NodeDetailDto:
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        plan = (
            self._repo.plan_version(
                user_id=user_id,
                task_id=task_id,
                version=run.plan_version,
            )
            if run is not None
            else self._repo.latest_plan(user_id=user_id, task_id=task_id)
        )
        if plan is None:
            raise NotFoundError("资源不存在")
        graph = (plan.payload or {}).get("graph") or {}
        raw_nodes = graph.get("nodes") or []
        node = next((n for n in raw_nodes if str(n.get("node_id") or "") == node_id), None)
        if node is None:
            raise NotFoundError("资源不存在")
        node_type = str(node.get("node_type") or "")
        event_facts = self._event_facts(
            user_id=user_id,
            task_id=task_id,
            run_id=run.id if run is not None else None,
        )
        urls = self._run_url_stats(
            user_id=user_id,
            task_id=task_id,
            run=run,
            event_url_states=event_facts.url_states,
        )
        record_total = self._run_record_count(user_id=user_id, task_id=task_id, run=run)
        node_facts = {
            persisted_node.node_id: (persisted_node, attempt)
            for persisted_node, attempt in self._node_facts(
                user_id=user_id,
                task_id=task_id,
                run_id=run.id if run is not None else None,
            )
        }
        return NodeDetailDto(
            node_id=node_id,
            node_type=node_type,
            definition_version=str(node.get("definition_version") or ""),
            resource_class=self._resource_class(node_type),
            depends_on=[str(d) for d in (node.get("depends_on") or [])],
            optional=bool(node.get("optional")),
            fail_policy=str(node.get("fail_policy") or "block"),
            plan_version=plan.version,
            stage=self._node_stage(node_type).value,
            run=(
                RunSummary(
                    run_id=run.id,
                    state=run.state,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    plan_version=run.plan_version,
                    spec_version=run.spec_version,
                )
                if run
                else None
            ),
            parameters_summary=self._parameters_summary(node.get("parameters") or {}),
            execution=self._node_execution(
                node_type=node_type,
                event_facts=event_facts.nodes.get(node_id),
                urls=urls,
                record_total=record_total,
                node_fact=node_facts.get(node_id),
            ),
        )

    @staticmethod
    def _resource_class(node_type: str) -> str | None:
        from app.plan.nodes import NodeRegistry

        definition = NodeRegistry().get(node_type)
        return definition.resource_class.value if definition else None

    def _node_stage(self, node_type: str) -> StageKey:
        return _NODE_TYPE_STAGE.get(node_type, StageKey.GOAL_PLAN)

    def _node_execution(
        self,
        *,
        node_type: str,
        event_facts: _NodeEventFacts | None,
        urls: dict[str, int],
        record_total: int,
        node_fact: tuple[Any, Any | None] | None,
    ) -> DagNodeExecution:
        """Prefer canonical NodeRun/Attempt state and supplement it with safe event stats."""
        event_facts = event_facts or _NodeEventFacts()
        persisted_node, persisted_attempt = node_fact or (None, None)
        last_status = (
            persisted_node.state if persisted_node is not None else event_facts.last_status
        )
        last_error = (
            persisted_attempt.error_code
            if persisted_attempt is not None
            else event_facts.last_error
        )
        persisted_duration_ms = self._duration_ms(persisted_node)
        duration_ms = (
            persisted_duration_ms if persisted_duration_ms is not None else event_facts.duration_ms
        )
        attempt_count = (
            persisted_attempt.attempt
            if persisted_attempt is not None
            else event_facts.attempt_count
        )
        url_fetched_count = urls["fetched"] + urls["failed"] if node_type == "fetch" else 0
        record_count = (
            record_total if node_type in ("extract", "normalize", "deduplicate", "validate") else 0
        )
        return DagNodeExecution(
            event_count=event_facts.event_count,
            last_status=last_status,
            last_error=last_error,
            attempt_count=attempt_count,
            tool=event_facts.tool,
            model=event_facts.model,
            duration_ms=duration_ms,
            tokens_in=event_facts.tokens_in,
            tokens_out=event_facts.tokens_out,
            url_fetched_count=url_fetched_count,
            record_count=record_count,
        )

    @staticmethod
    def _duration_ms(node: Any | None) -> int | None:
        if node is None or node.started_at is None or node.finished_at is None:
            return None
        return max(0, int((node.finished_at - node.started_at).total_seconds() * 1000))

    @staticmethod
    def _parameters_summary(parameters: dict) -> dict:
        """参数摘要：只暴露标量、非 secret 键；凭据引用等一律不返回。"""
        out: dict = {}
        for key, value in parameters.items():
            if key in _SECRET_KEYS:
                continue
            if isinstance(value, (str, int, float, bool)) and value is not None:
                out[key] = value
        return out

    def _stage_state_from_facts(
        self,
        *,
        key: StageKey,
        stage_event_counts: dict[StageKey, int],
        run_state: str | None,
    ) -> StageState:
        if stage_event_counts[key] == 0:
            return "not_started"
        if run_state == "FAILED":
            return "failed"
        completed = run_state in ("COMPLETED", "PARTIALLY_COMPLETED", "FAILED") or any(
            stage_event_counts[later] > 0 for later in _STAGE_ORDER[_STAGE_ORDER.index(key) + 1 :]
        )
        if run_state == "PARTIALLY_COMPLETED":
            return "partial" if completed else "in_progress"
        return "completed" if completed else "in_progress"

    # ---- timeline ----
    def timeline(
        self, *, user_id: int, task_id: int, category: str | None, after_id: int, limit: int
    ) -> TimelinePage:
        cursor = after_id or 0
        matched: list[TimelineEvent] = []
        while True:
            batch = self._repo.events_after(
                user_id=user_id, task_id=task_id, after_id=cursor, limit=_BATCH_SIZE
            )
            if not batch:
                break
            cursor = batch[-1].id
            for ev in batch:
                dto = self._to_dto(ev)
                if category is None or category in dto.categories:
                    matched.append(dto)
            if len(batch) < _BATCH_SIZE:
                break
        page = matched[:limit]
        has_more = len(matched) > limit
        return TimelinePage(
            task_id=task_id,
            items=page,
            next_cursor=page[-1].event_id if page else None,
            has_more=has_more,
        )

    # ---- event classification / mapping ----
    def _stage(self, ev: Any) -> StageKey:
        payload = ev.payload or {}
        node_type = payload.get("node_type")
        if node_type:
            mapped = _NODE_TYPE_STAGE.get(str(node_type))
            if mapped is not None:
                return mapped
        event_type = ev.event_type or ""
        if event_type.startswith("discovery."):
            return StageKey.SOURCE_DISCOVERY
        if event_type.startswith("fetch."):
            return StageKey.FETCH
        if event_type.startswith("extraction.") or event_type.startswith("normalize."):
            return StageKey.EXTRACTION
        if event_type.startswith("validation.") or event_type.startswith("record."):
            return StageKey.VALIDATION
        return StageKey.GOAL_PLAN

    def _classify(self, ev: Any) -> list[TimelineCategory]:
        payload = ev.payload or {}
        event_type = ev.event_type or ""
        cats: list[TimelineCategory] = []
        if (
            event_type in _ERROR_TYPES
            or (payload.get("status") or payload.get("state")) == "FAILED"
            or payload.get("error_code")
            or payload.get("reason_code")
        ):
            cats.append("error")
        if _safe_int(payload.get("retry_count")) > 0 or _safe_int(payload.get("attempt")) > 1:
            cats.append("retry")
        if event_type in _TOOL_UPGRADE_TYPES:
            cats.append("tool_upgrade")
        if event_type in _PLAN_CHANGE_TYPES or event_type.startswith("approval."):
            cats.append("plan_change")
        if (
            payload.get("model")
            or payload.get("tokens_in") is not None
            or payload.get("tokens_out") is not None
            or payload.get("token") is not None
        ):
            cats.append("model_call")
        if event_type in _PAUSE_RESUME_TYPES:
            cats.append("pause_resume")
        return cats

    def _summary(self, ev: Any) -> str:
        payload = ev.payload or {}
        event_type = ev.event_type or ""
        if event_type == "task.plan_generated":
            return f"已生成计划 v{payload.get('plan_version')}"
        if event_type == "task.plan_replanned":
            return f"计划已调整 v{payload.get('plan_version')}"
        if event_type.startswith("task."):
            return _TASK_EVENT_LABELS.get(event_type, f"任务事件：{event_type}")
        if event_type.startswith("approval."):
            return f"审批：{event_type.removeprefix('approval.')}"
        if event_type == "fetch.completed":
            return f"抓取完成（{payload.get('tool') or 'http'}）"
        if event_type == "fetch.failed":
            return "抓取失败"
        if event_type == "fetch.escalated":
            return "升级到浏览器渲染"
        if event_type == "fetch.credential_required":
            return "需要网站凭据"
        if event_type == "node.blocked_high_risk":
            return "高风险节点已阻止"
        if event_type in _RUN_EVENT_LABELS:
            return _RUN_EVENT_LABELS[event_type]
        if event_type == "extraction.llm_fallback_used":
            return f"LLM 语义提取（{payload.get('model') or '—'}）"
        if event_type.startswith("discovery."):
            return _DISCOVERY_LABELS.get(event_type, "来源发现")
        if event_type.startswith("record."):
            return _RECORD_LABELS.get(event_type, "记录变更")
        if event_type in _NODE_RESOURCE_LABELS:
            return _NODE_RESOURCE_LABELS[event_type]
        return event_type

    def _to_dto(self, ev: Any) -> TimelineEvent:
        payload = ev.payload or {}
        node_id = str(payload["node_id"]) if payload.get("node_id") is not None else None
        attempt = _safe_int(payload.get("attempt"))
        retry_count = max(_safe_int(payload.get("retry_count")), attempt - 1 if attempt > 1 else 0)
        refs: list[int] = []
        raw_refs = payload.get("evidence_refs") or []
        if isinstance(raw_refs, int):
            raw_refs = [raw_refs]
        if isinstance(raw_refs, list):
            refs.extend(_safe_int(r) for r in raw_refs)
        for key in ("snapshot_id", "record_id"):
            if payload.get(key) is not None:
                refs.append(_safe_int(payload.get(key)))
        return TimelineEvent(
            event_id=ev.id,
            timestamp=ev.occurred_at,
            categories=self._classify(ev),
            stage=self._stage(ev).value,
            summary=self._summary(ev),
            status=(
                str(payload.get("status") or payload.get("state"))
                if payload.get("status") is not None or payload.get("state") is not None
                else None
            ),
            error_code=(
                str(payload.get("error_code") or payload.get("reason_code"))
                if payload.get("error_code") is not None or payload.get("reason_code") is not None
                else None
            ),
            run_id=ev.run_id,
            node_run_id=ev.node_run_id,
            node_id=node_id,
            retry_count=retry_count,
            tool=str(payload["tool"]) if payload.get("tool") else None,
            model=str(payload["model"]) if payload.get("model") else None,
            duration_ms=_safe_int(payload.get("duration_ms")) or None,
            tokens_in=_safe_int(payload.get("tokens_in")) or None,
            tokens_out=_safe_int(payload.get("tokens_out")) or None,
            evidence_refs=refs,
            trace_ref=str(payload["trace_id"]) if payload.get("trace_id") else None,
        )
