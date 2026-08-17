"""M-12 CompletionDecision（D-006 / 模块需求 43-52）。

定向范围完成 + 探索饱和 + 混合两阶段 + 部分完成。Completion 由 scope / quality /
saturation / resource limits / completed work 共同决定，而不是只由固定记录数或单一
task_type 判定。四路 typed decision：

- ``COMPLETED``：达到业务完成条件 + 质量条件 + 当前 scope 已正确处理。
- ``CONTINUE``：当前 round 已完成但最低合格/饱和/覆盖尚未满足，且仍有搜索轮次/页面额度/运行时间。
- ``PARTIALLY_COMPLETED``：业务目标未完全满足，但已达到合法运行边界且存在可用的 committed work。
- ``FAILED``：不可恢复错误 + 无可构成可用结果的 completed work（``CompletionIncompleteError``）。

「任务停止采集」与「数据质量高」分开表达（模块需求 52）：scope complete 但大量 REJECTED
→ CompletionDecision=scope complete + QualityMetrics 差；不因 quality 差让 Workflow
永不结束，也不因采集完成把坏数据自动 PASSED。禁止人民币/美元/token 金额作为完成条件
（D-036 / 模块需求 51）。

HYBRID 是两阶段任务（D-003/D-077）：Phase A 来源发现 → Phase B 指定来源有界采集。
不能把整个 HYBRID 当 EXPLORATORY，也不能当 SPECIFIED_SOURCE。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class CompletionOutcome(StrEnum):
    """机器可读的四路完成判定。FAILED 由 CompletionIncompleteError 表达（Activity 映射）。"""

    COMPLETED = "COMPLETED"
    CONTINUE = "CONTINUE"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"


class CompletionIncompleteError(Exception):
    """无可构成可用结果的 completed work 时，任务不可恢复地无法完成（= FAILED）。"""

    code = "INCOMPLETE_WITHOUT_COMPLETED_WORK"

    def __init__(self) -> None:
        super().__init__(self.code)


class CompletionDecisionView(BaseModel):
    model_config = _STRICT

    outcome: CompletionOutcome
    status: str  # NORMAL_COMPLETED | PARTIALLY_COMPLETED | CONTINUE（持久化行语义）
    reason: str
    is_partial: bool
    completion_type: str  # directional_scope_complete | exploratory_saturation |
    # hybrid_target_met | search_more_required | resource_limit_reached_with_results |
    # runtime_limit | user_stopped | access_limited | partial_source_failure
    qualified_record_count: int
    saturation_evidence: dict = {}
    runtime_limit_reason: str | None = None
    scope_completion_metadata: dict = {}
    continue_hints: dict = {}


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
        fetched_page_count: int,
        record_count: int,
        batch_unique_counts: list[int],
        qualified_record_count: int,
        runtime_limit_reason: str | None,
        user_stopped: bool,
        settings,
        access_limited_reason: str | None = None,
        search_round_count: int = 1,
        max_search_rounds: int | None = None,
    ) -> CompletionDecisionView:
        task_type = spec_payload.get("task_type")
        conditions = spec_payload.get("completion_conditions") or []
        min_records = next(
            (c.get("target") for c in conditions if c.get("kind") == "min_records"), 0
        )
        scope_done = eligible_url_count > 0 and terminal_url_count >= eligible_url_count
        completed_work = fetched_page_count > 0 or record_count > 0
        scope_metadata = {
            "eligible_urls": eligible_url_count,
            "terminal_urls": terminal_url_count,
            "fetched_pages": fetched_page_count,
            "records": record_count,
            "scope_complete": scope_done,
        }
        remaining = (
            None if max_search_rounds is None else max(0, max_search_rounds - search_round_count)
        )
        has_remaining = remaining is None or remaining > 0
        reached_min = qualified_record_count >= max(
            min_records, settings.min_qualified_records_for_saturation
        )

        def _view(
            outcome: CompletionOutcome,
            status: str,
            reason: str,
            completion_type: str,
            *,
            is_partial: bool = False,
            saturation_evidence: dict | None = None,
            runtime_limit_reason_value: str | None = None,
            continue_hints: dict | None = None,
        ) -> CompletionDecisionView:
            return CompletionDecisionView(
                outcome=outcome,
                status=status,
                reason=reason,
                is_partial=is_partial,
                completion_type=completion_type,
                qualified_record_count=qualified_record_count,
                saturation_evidence=saturation_evidence or {},
                runtime_limit_reason=runtime_limit_reason_value,
                scope_completion_metadata=scope_metadata,
                continue_hints=continue_hints or {},
            )

        def _continue_view() -> CompletionDecisionView:
            return _view(
                CompletionOutcome.CONTINUE,
                "CONTINUE",
                "当前结果不足，继续发现来源",
                "search_more_required",
                continue_hints={
                    "reason": "SEARCH_MORE_REQUIRED",
                    "search_round_count": search_round_count,
                    "max_search_rounds": max_search_rounds,
                    "remaining_search_rounds": remaining,
                    "qualified_record_count": qualified_record_count,
                    "min_qualified_records": max(
                        min_records, settings.min_qualified_records_for_saturation
                    ),
                    "scope_complete": scope_done,
                },
            )

        # 空来源发现是成功且显式的结果，优先于限制/停止（无 completed subset 存在）。
        if eligible_url_count == 0 and fetched_page_count == 0:
            return _view(
                CompletionOutcome.COMPLETED,
                "NORMAL_COMPLETED",
                "未发现符合范围的页面",
                "NO_MATCHING_PAGES",
            )
        # 无可构成可用结果的 completed work → 不可恢复 FAILED。
        if not completed_work:
            raise CompletionIncompleteError()
        if scope_done and fetched_page_count > 0 and record_count == 0:
            return _view(
                CompletionOutcome.COMPLETED,
                "NORMAL_COMPLETED",
                "已处理页面但没有符合条件的记录",
                "NO_MATCHING_RECORDS",
            )
        # 无金额条件（模块需求 51）：只允许 max_pages/max_duration/retry limit/范围/饱和。
        # 硬停止优先于 CONTINUE：达到资源/用户边界即保留已提交结果。
        if runtime_limit_reason and completed_work:
            return _view(
                CompletionOutcome.PARTIALLY_COMPLETED,
                "PARTIALLY_COMPLETED",
                runtime_limit_reason,
                "runtime_limit",
                is_partial=True,
                runtime_limit_reason_value=runtime_limit_reason,
            )
        if user_stopped and completed_work:
            return _view(
                CompletionOutcome.PARTIALLY_COMPLETED,
                "PARTIALLY_COMPLETED",
                "用户停止且已有提交结果",
                "user_stopped",
                is_partial=True,
            )
        if task_type == "SPECIFIED_SOURCE":
            if access_limited_reason and completed_work:
                return _view(
                    CompletionOutcome.PARTIALLY_COMPLETED,
                    "PARTIALLY_COMPLETED",
                    access_limited_reason,
                    "access_limited",
                    is_partial=True,
                )
            # 定向：范围中 eligible URL 全部进入 terminal state（范围完成，模块需求 44）。
            # 定向无搜索可继续，未范围完成且无停止理由 → 不可恢复 FAILED。
            if scope_done:
                return _view(
                    CompletionOutcome.COMPLETED,
                    "NORMAL_COMPLETED",
                    "指定来源范围已全部处理",
                    "directional_scope_complete",
                )
            raise CompletionIncompleteError()
        if task_type == "EXPLORATORY":
            # 探索：最低合格 PASSED 数 + 信息饱和（模块需求 45/48）。
            saturated = SaturationTracker(
                settings.saturation_batch_window, settings.saturation_new_unique_threshold
            ).is_saturated(batch_unique_counts)
            if reached_min and saturated:
                return _view(
                    CompletionOutcome.COMPLETED,
                    "NORMAL_COMPLETED",
                    "达到最低合格记录且信息饱和",
                    "exploratory_saturation",
                    saturation_evidence={
                        "recent_batch_unique_counts": batch_unique_counts,
                        "saturated": True,
                    },
                )
            if has_remaining:
                return _continue_view()
            if access_limited_reason:
                return _view(
                    CompletionOutcome.PARTIALLY_COMPLETED,
                    "PARTIALLY_COMPLETED",
                    access_limited_reason,
                    "access_limited",
                    is_partial=True,
                )
            return _view(
                CompletionOutcome.PARTIALLY_COMPLETED,
                "PARTIALLY_COMPLETED",
                "已达到运行边界，保留部分结果",
                "resource_limit_reached_with_results",
                is_partial=True,
            )
        if task_type == "HYBRID":
            # 混合两阶段（D-003/D-077）：Phase A 发现目标来源 + Phase B 指定来源有界采集。
            # 完成 = 达到最低合格记录 且 当前已发现 scope 全部处理。
            if reached_min and scope_done:
                return _view(
                    CompletionOutcome.COMPLETED,
                    "NORMAL_COMPLETED",
                    "目标来源已发现且范围内已全部处理，达到最低合格记录",
                    "hybrid_target_met",
                )
            if has_remaining:
                return _continue_view()
            if access_limited_reason:
                return _view(
                    CompletionOutcome.PARTIALLY_COMPLETED,
                    "PARTIALLY_COMPLETED",
                    access_limited_reason,
                    "access_limited",
                    is_partial=True,
                )
            return _view(
                CompletionOutcome.PARTIALLY_COMPLETED,
                "PARTIALLY_COMPLETED",
                "已达到运行边界，保留部分结果",
                "resource_limit_reached_with_results",
                is_partial=True,
            )
        raise CompletionIncompleteError()


__all__ = [
    "CompletionDecisionService",
    "CompletionDecisionView",
    "CompletionIncompleteError",
    "CompletionOutcome",
    "SaturationTracker",
]
