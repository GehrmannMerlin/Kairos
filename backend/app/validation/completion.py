"""M-12 CompletionDecision（D-006 / 模块需求 43-52）：定向范围完成 + 探索饱和 + 部分完成。

「任务停止采集」与「数据质量高」分开表达（模块需求 52）：scope complete 但大量 REJECTED
→ CompletionDecision=scope complete + QualityMetrics 差；不因 quality 差让 Workflow
永不结束，也不因采集完成把坏数据自动 PASSED。禁止人民币/美元/token 金额作为完成条件
（D-036 / 模块需求 51）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class CompletionDecisionView(BaseModel):
    model_config = _STRICT

    status: str  # NORMAL_COMPLETED | PARTIALLY_COMPLETED
    reason: str
    is_partial: bool
    completion_type: str  # directional_scope_complete | exploratory_saturation |
    # runtime_limit | user_stopped | access_limited | partial_source_failure
    qualified_record_count: int
    saturation_evidence: dict = {}
    runtime_limit_reason: str | None = None
    scope_completion_metadata: dict = {}


class SaturationTracker:
    """deterministic 探索饱和（D-006 / 模块需求 46-47）：最近 N batch 新增 unique 记录增量。"""

    def __init__(self, window: int = 3, threshold: float = 0.0) -> None:
        self._window = window
        self._threshold = threshold

    def is_saturated(self, batch_unique_counts: list[int]) -> bool:
        if len(batch_unique_counts) < self._window:
            return False
        recent = batch_unique_counts[-self._window :]
        return sum(recent) / len(recent) <= self._threshold


class CompletionDecisionService:
    def decide(
        self,
        *,
        run: Any,
        spec_payload: dict,
        partition_counts: dict,
        eligible_url_count: int,
        terminal_url_count: int,
        batch_unique_counts: list[int],
        qualified_record_count: int,
        runtime_limit_reason: str | None,
        user_stopped: bool,
        settings,
    ) -> CompletionDecisionView:
        task_type = spec_payload.get("task_type")
        conditions = spec_payload.get("completion_conditions") or []
        min_records = next(
            (c.get("target") for c in conditions if c.get("kind") == "min_records"), 0
        )
        # 无金额条件（模块需求 51）：只允许 max_pages/max_duration/retry limit/范围/饱和
        if runtime_limit_reason:
            return CompletionDecisionView(
                status="PARTIALLY_COMPLETED",
                reason=runtime_limit_reason,
                is_partial=True,
                completion_type="runtime_limit",
                qualified_record_count=qualified_record_count,
                runtime_limit_reason=runtime_limit_reason,
                scope_completion_metadata={
                    "eligible_urls": eligible_url_count,
                    "terminal_urls": terminal_url_count,
                },
            )
        if user_stopped:
            return CompletionDecisionView(
                status="PARTIALLY_COMPLETED",
                reason="用户停止且已有提交结果",
                is_partial=True,
                completion_type="user_stopped",
                qualified_record_count=qualified_record_count,
                scope_completion_metadata={
                    "eligible_urls": eligible_url_count,
                    "terminal_urls": terminal_url_count,
                },
            )
        if task_type == "SPECIFIED_SOURCE":
            # 定向：范围中 eligible URL 全部进入 terminal state（范围完成，模块需求 44）
            scope_done = eligible_url_count > 0 and terminal_url_count >= eligible_url_count
            return CompletionDecisionView(
                status="NORMAL_COMPLETED" if scope_done else "PARTIALLY_COMPLETED",
                reason="指定来源范围已全部处理" if scope_done else "指定来源范围未完整处理",
                is_partial=not scope_done,
                completion_type="directional_scope_complete" if scope_done else "access_limited",
                qualified_record_count=qualified_record_count,
                scope_completion_metadata={
                    "eligible_urls": eligible_url_count,
                    "terminal_urls": terminal_url_count,
                    "scope_complete": scope_done,
                },
            )
        # EXPLORATORY：最低合格 PASSED 数 + 信息饱和（模块需求 45/48）
        saturated = SaturationTracker(
            settings.saturation_batch_window, settings.saturation_new_unique_threshold
        ).is_saturated(batch_unique_counts)
        reached_min = qualified_record_count >= max(
            min_records, settings.min_qualified_records_for_saturation
        )
        if reached_min and saturated:
            return CompletionDecisionView(
                status="NORMAL_COMPLETED",
                reason="达到最低合格记录且信息饱和",
                is_partial=False,
                completion_type="exploratory_saturation",
                qualified_record_count=qualified_record_count,
                saturation_evidence={
                    "recent_batch_unique_counts": batch_unique_counts,
                    "saturated": True,
                },
            )
        return CompletionDecisionView(
            status="PARTIALLY_COMPLETED",
            reason="未达到最低合格记录或尚未饱和",
            is_partial=True,
            completion_type="access_limited",
            qualified_record_count=qualified_record_count,
            saturation_evidence={
                "recent_batch_unique_counts": batch_unique_counts,
                "saturated": saturated,
            },
        )


__all__ = ["CompletionDecisionView", "CompletionDecisionService", "SaturationTracker"]
