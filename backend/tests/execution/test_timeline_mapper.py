"""TimelineMapper：DomainEvent → TimelineEvent 的纯映射 spec。

期望值是手工固定（固化现有 REST timeline 行为），不是从 _to_dto 抄。
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.execution.contracts import TimelineEvent
from app.execution.timeline import TimelineMapper

_PAYLOAD_ALLOWED = {
    "node_id": "n3",
    "node_type": "fetch",
    "attempt": 1,
    "status": "COMPLETED",
    "counts": {"fetched": 3},
    "duration_ms": 1200,
}


def _ev(event_type: str, payload: dict, *, event_id: int = 1, run_id: int = 8) -> object:
    """构造与 SQLAlchemy DomainEvent 行字段兼容的轻量桩。"""
    return SimpleNamespace(
        id=event_id,
        occurred_at=datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC),
        event_type=event_type,
        run_id=run_id,
        node_run_id=None,
        payload=payload,
    )


def test_node_completed_rich_mapping() -> None:
    dto = TimelineMapper.to_timeline_event(_ev("run.node_completed", _PAYLOAD_ALLOWED))
    assert isinstance(dto, TimelineEvent)
    assert dto.event_id == 1
    assert dto.stage == "fetch"
    assert dto.summary == "节点已完成"
    assert dto.status == "COMPLETED"
    assert dto.node_id == "n3"
    assert dto.retry_count == 0
    assert dto.duration_ms == 1200
    assert dto.categories == []


def test_plan_generated_and_replanned_map_to_plan_change() -> None:
    generated = TimelineMapper.to_timeline_event(
        _ev("task.plan_generated", {"plan_version": 2}, event_id=1)
    )
    assert generated.stage == "goal_plan"
    assert generated.categories == ["plan_change"]
    assert generated.summary == "已生成计划 v2"

    replanned = TimelineMapper.to_timeline_event(
        _ev("task.plan_replanned", {"plan_version": 3}, event_id=2)
    )
    assert replanned.categories == ["plan_change"]
    assert replanned.summary == "计划已调整 v3"


def test_run_node_started_progress_and_blocked() -> None:
    started = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_started",
            {"node_id": "n3", "node_type": "fetch", "state": "RUNNING"},
            event_id=1,
        )
    )
    assert started.stage == "fetch"
    assert started.summary == "节点已开始"
    assert started.status == "RUNNING"
    assert started.categories == []

    progress = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_progress",
            {"node_id": "n3", "node_type": "fetch", "state": "RUNNING"},
            event_id=2,
        )
    )
    assert progress.summary == "节点进度已更新"

    blocked = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_blocked",
            {"node_id": "n3", "node_type": "fetch", "reason_code": "NETWORK"},
            event_id=3,
        )
    )
    assert blocked.summary == "节点已阻塞"
    assert blocked.categories == ["error"]


def test_run_node_failed_with_retry() -> None:
    dto = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_failed",
            {"node_id": "n3", "node_type": "fetch", "state": "FAILED", "attempt": 2},
            event_id=4,
        )
    )
    assert dto.summary == "节点执行失败"
    assert dto.status == "FAILED"
    assert dto.categories == ["error", "retry"]
    assert dto.retry_count == 1


def test_fetch_events_map_tool_tool_upgrade_and_error() -> None:
    completed = TimelineMapper.to_timeline_event(
        _ev("fetch.completed", {"tool": "httpx"}, event_id=1)
    )
    assert completed.stage == "fetch"
    assert completed.summary == "抓取完成（httpx）"
    assert completed.tool == "httpx"

    escalated = TimelineMapper.to_timeline_event(_ev("fetch.escalated", {}, event_id=2))
    assert escalated.categories == ["tool_upgrade"]
    assert escalated.summary == "升级到浏览器渲染"

    failed = TimelineMapper.to_timeline_event(
        _ev("fetch.failed", {"status": "FAILED", "error_code": "network_timeout"}, event_id=3)
    )
    assert failed.categories == ["error"]
    assert failed.summary == "抓取失败"
    assert failed.status == "FAILED"
    assert failed.error_code == "network_timeout"


def test_extraction_events_map_model_call_and_tool_upgrade() -> None:
    fallback = TimelineMapper.to_timeline_event(
        _ev(
            "extraction.llm_fallback_used",
            {"model": "deepseek-v4-flash", "tokens_in": 100},
            event_id=1,
        )
    )
    assert fallback.stage == "extraction"
    assert fallback.categories == ["tool_upgrade", "model_call"]
    assert fallback.summary == "LLM 语义提取（deepseek-v4-flash）"
    assert fallback.model == "deepseek-v4-flash"
    assert fallback.tokens_in == 100

    promoted = TimelineMapper.to_timeline_event(_ev("extraction.rule_promoted", {}, event_id=2))
    assert promoted.categories == ["tool_upgrade"]


def test_pause_and_resume_map_to_pause_resume() -> None:
    pause = TimelineMapper.to_timeline_event(_ev("task.pause", {}, event_id=1))
    assert pause.categories == ["pause_resume"]
    assert pause.summary == "请求暂停"

    resume = TimelineMapper.to_timeline_event(_ev("task.resume", {}, event_id=2))
    assert resume.categories == ["pause_resume"]
    assert resume.summary == "已恢复"


def test_record_events_map_summary_and_evidence_refs() -> None:
    approved = TimelineMapper.to_timeline_event(
        _ev("record.approved", {"snapshot_id": 5, "record_id": 7}, event_id=1)
    )
    assert approved.stage == "validation"
    assert approved.summary == "记录已通过"
    assert approved.evidence_refs == [5, 7]

    completed = TimelineMapper.to_timeline_event(
        _ev("record.completed", {"record_id": 7}, event_id=2)
    )
    assert completed.summary == "记录变更"
    assert completed.evidence_refs == [7]


def test_approval_created_maps_to_plan_change() -> None:
    dto = TimelineMapper.to_timeline_event(_ev("approval.created", {"approval_id": 3}, event_id=1))
    assert dto.categories == ["plan_change"]
    assert dto.summary == "审批：created"


def test_trace_id_maps_to_trace_ref() -> None:
    dto = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_started",
            {"node_id": "n3", "node_type": "fetch", "trace_id": "trace-abc"},
            event_id=1,
        )
    )
    assert dto.trace_ref == "trace-abc"


def test_payload_secrets_never_leak() -> None:
    dto = TimelineMapper.to_timeline_event(
        _ev(
            "run.node_completed",
            {
                **_PAYLOAD_ALLOWED,
                "api_key": "SK-SECRET",
                "cookie": "c=1",
                "authorization": "Bearer x",
                "token": "t",
            },
        )
    )
    text = dto.model_dump_json()
    assert "SK-SECRET" not in text and "Bearer" not in text and "c=1" not in text
    assert "token" not in dto.model_dump()  # _to_dto 不映射 token 字段
