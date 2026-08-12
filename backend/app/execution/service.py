"""M-14 Execution read-model service（D-055/D-063）。

阶段聚合来自 Run/DomainEvent/URLResource/Record 真实事实；NodeRun/NodeAttempt
在当前采集执行路径不产生行，因此不伪造 Node 级数据。Timeline 只暴露 allowlist
字段，payload 中的 secret 绝不进入响应。
"""

from __future__ import annotations

from typing import Any

from app.auth.errors import NotFoundError
from app.execution.contracts import (
    DagEdge,
    DagNodeDto,
    DagNodeExecution,
    DagView,
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

_ERROR_TYPES = {"task.fail", "fetch.failed", "extraction.failed", "node.blocked_high_risk"}
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

_BATCH_SIZE = 500


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


class ExecutionService:
    def __init__(self, db: Any) -> None:
        self._repo = ExecutionRepository(db)

    # ---- overview ----
    def assemble_overview(self, *, user_id: int, task_id: int) -> ExecutionView:
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        plan = self._repo.latest_plan(user_id=user_id, task_id=task_id)
        urls = self._repo.url_stats(user_id=user_id, task_id=task_id)
        records = self._repo.record_counts(user_id=user_id, task_id=task_id)
        record_total = sum(records.values())

        all_events = self._repo.events_after(
            user_id=user_id, task_id=task_id, after_id=0, limit=100_000
        )
        stage_events: dict[StageKey, list[Any]] = {k: [] for k in _STAGE_ORDER}
        for ev in all_events:
            stage_events[self._stage(ev)].append(ev)

        run_state = run.state if run else None
        stages: list[StageSummary] = []
        for key in _STAGE_ORDER:
            evs = stage_events[key]
            state = self._stage_state(
                key=key, stage_events=evs, all_events=all_events, run_state=run_state
            )
            error_count = sum(1 for ev in evs if "error" in self._classify(ev))
            url_processed = (
                urls["fetched"] + urls["failed"]
                if key == StageKey.FETCH
                else (urls["discovered"] if key == StageKey.SOURCE_DISCOVERY else 0)
            )
            record_count = record_total if key in (StageKey.EXTRACTION, StageKey.VALIDATION) else 0
            stages.append(
                StageSummary(
                    key=key,
                    label=_STAGE_LABELS[key],
                    state=state,
                    event_count=len(evs),
                    url_processed=url_processed,
                    record_count=record_count,
                    error_count=error_count,
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
        )

    # ---- plan DAG + node detail ----
    def assemble_dag(self, *, user_id: int, task_id: int) -> DagView:
        plan = self._repo.latest_plan(user_id=user_id, task_id=task_id)
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
        all_events = self._repo.events_after(
            user_id=user_id, task_id=task_id, after_id=0, limit=100_000
        )
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        urls = self._repo.url_stats(user_id=user_id, task_id=task_id)
        record_total = self._repo.record_count_total(user_id=user_id, task_id=task_id)
        run_state = run.state if run else None
        stage_status = {k.value: v for k, v in self._stage_state_map(all_events, run_state).items()}

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
                        node_id=node_id,
                        node_type=node_type,
                        all_events=all_events,
                        urls=urls,
                        record_total=record_total,
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

    def node_detail(
        self, *, user_id: int, task_id: int, node_id: str
    ) -> NodeDetailDto:
        plan = self._repo.latest_plan(user_id=user_id, task_id=task_id)
        if plan is None:
            raise NotFoundError("资源不存在")
        graph = (plan.payload or {}).get("graph") or {}
        raw_nodes = graph.get("nodes") or []
        node = next((n for n in raw_nodes if str(n.get("node_id") or "") == node_id), None)
        if node is None:
            raise NotFoundError("资源不存在")
        node_type = str(node.get("node_type") or "")
        all_events = self._repo.events_after(
            user_id=user_id, task_id=task_id, after_id=0, limit=100_000
        )
        run = self._repo.latest_run(user_id=user_id, task_id=task_id)
        urls = self._repo.url_stats(user_id=user_id, task_id=task_id)
        record_total = self._repo.record_count_total(user_id=user_id, task_id=task_id)
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
                node_id=node_id,
                node_type=node_type,
                all_events=all_events,
                urls=urls,
                record_total=record_total,
            ),
        )

    def _stage_state_map(self, all_events: list[Any], run_state: str | None) -> dict[StageKey, str]:
        stage_events: dict[StageKey, list[Any]] = {k: [] for k in _STAGE_ORDER}
        for ev in all_events:
            stage_events[self._stage(ev)].append(ev)
        return {
            key: self._stage_state(
                key=key, stage_events=stage_events[key], all_events=all_events, run_state=run_state
            )
            for key in _STAGE_ORDER
        }

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
        node_id: str,
        node_type: str,
        all_events: list[Any],
        urls: dict[str, int],
        record_total: int,
    ) -> DagNodeExecution:
        """节点级执行证据：当前执行路径不写 NodeRun 行且事件大多不带 node_id，
        因此 event_count 通常为 0；fetch/extract/validate 类型按任务级 URL/Record 事实展示。
        """
        node_events = [
            ev
            for ev in all_events
            if ev.payload and str(ev.payload.get("node_id") or "") == node_id
        ]
        last_status = None
        last_error = None
        tool = None
        duration_ms = None
        attempt_count = 0
        for ev in node_events:
            payload = ev.payload or {}
            if payload.get("status"):
                last_status = str(payload["status"])
            if payload.get("error_code"):
                last_error = str(payload["error_code"])
            if payload.get("tool"):
                tool = str(payload["tool"])
            if payload.get("duration_ms") is not None:
                duration_ms = _safe_int(payload["duration_ms"])
            attempt = _safe_int(payload.get("attempt"))
            attempt_count = max(attempt_count, attempt)
        url_fetched_count = (
            urls["fetched"] + urls["failed"] if node_type == "fetch" else 0
        )
        record_count = (
            record_total if node_type in ("extract", "normalize", "deduplicate", "validate") else 0
        )
        return DagNodeExecution(
            event_count=len(node_events),
            last_status=last_status,
            last_error=last_error,
            attempt_count=attempt_count,
            tool=tool,
            duration_ms=duration_ms,
            url_fetched_count=url_fetched_count,
            record_count=record_count,
        )

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

    def _stage_state(
        self,
        *,
        key: StageKey,
        stage_events: list[Any],
        all_events: list[Any],
        run_state: str | None,
    ) -> StageState:
        if not stage_events:
            return "not_started"
        if run_state == "FAILED":
            return "failed"
        completed = self._stage_completed(key, all_events, run_state)
        if run_state == "PARTIALLY_COMPLETED":
            return "partial" if completed else "in_progress"
        return "completed" if completed else "in_progress"

    def _stage_completed(self, key: StageKey, all_events: list[Any], run_state: str | None) -> bool:
        if run_state in ("COMPLETED", "PARTIALLY_COMPLETED", "FAILED"):
            return True
        idx = _STAGE_ORDER.index(key)
        for later in _STAGE_ORDER[idx + 1 :]:
            if any(self._stage(ev) == later for ev in all_events):
                return True
        return False

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
            or payload.get("status") == "FAILED"
            or payload.get("error_code")
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
        if event_type == "extraction.llm_fallback_used":
            return f"LLM 语义提取（{payload.get('model') or '—'}）"
        if event_type.startswith("discovery."):
            return _DISCOVERY_LABELS.get(event_type, "来源发现")
        if event_type.startswith("record."):
            return _RECORD_LABELS.get(event_type, "记录变更")
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
            status=str(payload["status"]) if payload.get("status") is not None else None,
            error_code=(
                str(payload["error_code"]) if payload.get("error_code") is not None else None
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
