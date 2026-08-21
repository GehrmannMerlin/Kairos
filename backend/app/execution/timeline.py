"""执行时间线共享映射：DomainEvent → TimelineEvent（纯函数，无 DB）。

从 ExecutionService 原样抽取，REST timeline 与 timeline stream 共用同一映射，
保证分页查询与实时流输出完全一致。禁止透传 payload 原始字段。
"""
from __future__ import annotations

from typing import Any

from app.execution.contracts import (
    StageKey,
    TimelineCategory,
    TimelineEvent,
)

# 常量原样移动（_SECRET_KEYS / _NODE_TYPE_STAGE / _ERROR_TYPES / _TOOL_UPGRADE_TYPES /
# _PLAN_CHANGE_TYPES / _PAUSE_RESUME_TYPES / _TASK_EVENT_LABELS / _DISCOVERY_LABELS /
# _RECORD_LABELS / _NODE_RESOURCE_LABELS / _RUN_EVENT_LABELS 逐字从 service.py 复制）
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


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


class TimelineMapper:
    @classmethod
    def stage(cls, ev: Any) -> StageKey:
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

    @classmethod
    def classify(cls, ev: Any) -> list[TimelineCategory]:
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

    @classmethod
    def summary(cls, ev: Any) -> str:
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

    @classmethod
    def to_timeline_event(cls, ev: Any) -> TimelineEvent:
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
            categories=TimelineMapper.classify(ev),
            stage=TimelineMapper.stage(ev).value,
            summary=TimelineMapper.summary(ev),
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
