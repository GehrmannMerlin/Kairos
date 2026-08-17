"""CORE TEST F — Completion（模块需求 74）：定向范围完成 / 探索饱和 / 部分完成 / 无金额条件。"""

from __future__ import annotations

import pytest
from app.validation.completion import (
    CompletionDecisionService,
    CompletionIncompleteError,
    CompletionOutcome,
    SaturationTracker,
)
from app.validation.policies import ValidationSettings


def _spec(task_type: str, min_records: int = 0) -> dict:
    conditions = [{"kind": "min_records", "target": min_records}] if min_records else []
    return {"task_type": task_type, "completion_conditions": conditions}


def test_directional_scope_complete_is_normal():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("SPECIFIED_SOURCE"),
        partition_counts={"passed": 2},
        eligible_url_count=3,
        terminal_url_count=3,
        fetched_page_count=3,
        record_count=2,
        batch_unique_counts=[],
        qualified_record_count=2,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.is_partial is False
    assert d.completion_type == "directional_scope_complete"


def test_directional_scope_incomplete_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("SPECIFIED_SOURCE"),
        partition_counts={"passed": 0},
        eligible_url_count=3,
        terminal_url_count=1,
        fetched_page_count=1,
        record_count=0,
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
        access_limited_reason="access_limited",
    )
    assert d.status == "PARTIALLY_COMPLETED"
    assert d.is_partial is True
    assert d.completion_type == "access_limited"


def test_zero_eligible_urls_is_empty_success_not_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("HYBRID"),
        partition_counts={"passed": 0},
        eligible_url_count=0,
        terminal_url_count=0,
        fetched_page_count=0,
        record_count=0,
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.completion_type == "NO_MATCHING_PAGES"
    assert d.is_partial is False


def test_processed_pages_without_records_is_empty_record_success():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("SPECIFIED_SOURCE"),
        partition_counts={"passed": 0},
        eligible_url_count=3,
        terminal_url_count=3,
        fetched_page_count=3,
        record_count=0,
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.completion_type == "NO_MATCHING_RECORDS"
    assert d.is_partial is False


def test_runtime_limit_without_completed_work_is_not_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("SPECIFIED_SOURCE"),
        partition_counts={"passed": 0},
        eligible_url_count=0,
        terminal_url_count=0,
        fetched_page_count=0,
        record_count=0,
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason="max_pages_reached",
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.is_partial is False


@pytest.mark.parametrize("task_type", ["SPECIFIED_SOURCE", "EXPLORATORY", "HYBRID"])
def test_incomplete_scope_without_completed_work_is_not_partial(task_type: str):
    with pytest.raises(CompletionIncompleteError, match="INCOMPLETE_WITHOUT_COMPLETED_WORK"):
        CompletionDecisionService().decide(
            run=None,
            spec_payload=_spec(task_type),
            partition_counts={"passed": 0},
            eligible_url_count=5,
            terminal_url_count=0,
            fetched_page_count=0,
            record_count=0,
            batch_unique_counts=[],
            qualified_record_count=0,
            runtime_limit_reason=None,
            user_stopped=False,
            settings=ValidationSettings(),
        )


def test_specified_source_incomplete_scope_without_an_explicit_stop_is_not_partial():
    with pytest.raises(CompletionIncompleteError, match="INCOMPLETE_WITHOUT_COMPLETED_WORK"):
        CompletionDecisionService().decide(
            run=None,
            spec_payload=_spec("SPECIFIED_SOURCE"),
            partition_counts={"passed": 0},
            eligible_url_count=5,
            terminal_url_count=0,
            fetched_page_count=1,
            record_count=0,
            batch_unique_counts=[],
            qualified_record_count=0,
            runtime_limit_reason=None,
            user_stopped=False,
            settings=ValidationSettings(),
        )


@pytest.mark.parametrize("task_type", ["EXPLORATORY", "HYBRID"])
def test_discovery_task_incomplete_scope_with_remaining_capacity_continues(task_type: str):
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec(task_type),
        partition_counts={"passed": 0},
        eligible_url_count=5,
        terminal_url_count=0,
        fetched_page_count=1,
        record_count=0,
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.outcome == CompletionOutcome.CONTINUE


def test_exploratory_completion_metadata_persists_real_counts():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=1),
        partition_counts={"passed": 2},
        eligible_url_count=5,
        terminal_url_count=5,
        fetched_page_count=5,
        record_count=2,
        batch_unique_counts=[0, 0, 0],
        qualified_record_count=2,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.completion_type == "exploratory_saturation"
    assert d.scope_completion_metadata == {
        "eligible_urls": 5,
        "terminal_urls": 5,
        "fetched_pages": 5,
        "records": 2,
        "scope_complete": True,
    }


def test_exploratory_min_records_and_saturation_is_normal():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=3),
        partition_counts={"passed": 4},
        eligible_url_count=10,
        terminal_url_count=10,
        fetched_page_count=10,
        record_count=4,
        batch_unique_counts=[0, 0, 0],  # 最近 3 batch 新增 unique 率 0 → 饱和
        qualified_record_count=4,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.completion_type == "exploratory_saturation"
    assert d.saturation_evidence["saturated"] is True


def test_exploratory_not_saturated_with_remaining_capacity_continues():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=1),
        partition_counts={"passed": 1},
        eligible_url_count=10,
        terminal_url_count=5,
        fetched_page_count=5,
        record_count=1,
        batch_unique_counts=[3, 2, 1],  # 新增 unique 率 1~3 > 0 → 未饱和
        qualified_record_count=1,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
        access_limited_reason="access_limited",
    )
    assert d.outcome == CompletionOutcome.CONTINUE
    assert d.completion_type == "search_more_required"
    assert d.continue_hints["reason"] == "SEARCH_MORE_REQUIRED"


def test_runtime_limit_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=10),
        partition_counts={"passed": 2},
        eligible_url_count=10,
        terminal_url_count=4,
        fetched_page_count=4,
        record_count=2,
        batch_unique_counts=[],
        qualified_record_count=2,
        runtime_limit_reason="max_pages_reached",
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "PARTIALLY_COMPLETED"
    assert d.completion_type == "runtime_limit"
    assert d.runtime_limit_reason == "max_pages_reached"


def test_user_stopped_is_partial_and_keeps_committed():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("SPECIFIED_SOURCE"),
        partition_counts={"passed": 5},
        eligible_url_count=10,
        terminal_url_count=10,
        fetched_page_count=10,
        record_count=5,
        batch_unique_counts=[],
        qualified_record_count=5,
        runtime_limit_reason=None,
        user_stopped=True,
        settings=ValidationSettings(),
    )
    assert d.status == "PARTIALLY_COMPLETED"
    assert d.completion_type == "user_stopped"
    assert d.qualified_record_count == 5  # 已提交结果保留


def test_saturation_tracker_deterministic():
    t = SaturationTracker(window=3, threshold=0.0)
    assert t.is_saturated([0, 0, 0]) is True
    assert t.is_saturated([3, 2, 1]) is False
    assert t.is_saturated([1, 1]) is False  # 不足 window


def test_hybrid_target_met_and_scope_done_is_completed():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("HYBRID", min_records=3),
        partition_counts={"passed": 3},
        eligible_url_count=4,
        terminal_url_count=4,
        fetched_page_count=4,
        record_count=3,
        batch_unique_counts=[],
        qualified_record_count=3,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.outcome == CompletionOutcome.COMPLETED
    assert d.completion_type == "hybrid_target_met"
    assert d.is_partial is False


def test_hybrid_first_round_insufficient_with_remaining_capacity_continues():
    # Task 104 语义：1 PASSED + 2 NEEDS_REVIEW，min 未达，仍有搜索轮次 → CONTINUE（不能 FAILED）。
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("HYBRID", min_records=5),
        partition_counts={"passed": 1, "needs_review": 2},
        eligible_url_count=4,
        terminal_url_count=4,
        fetched_page_count=4,
        record_count=3,
        batch_unique_counts=[],
        qualified_record_count=1,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
        search_round_count=1,
        max_search_rounds=3,
    )
    assert d.outcome == CompletionOutcome.CONTINUE
    assert d.completion_type == "search_more_required"
    assert d.continue_hints["remaining_search_rounds"] == 2


def test_hybrid_exhausted_search_rounds_with_results_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("HYBRID", min_records=5),
        partition_counts={"passed": 1, "needs_review": 2},
        eligible_url_count=4,
        terminal_url_count=4,
        fetched_page_count=4,
        record_count=3,
        batch_unique_counts=[],
        qualified_record_count=1,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
        search_round_count=3,
        max_search_rounds=3,
    )
    assert d.outcome == CompletionOutcome.PARTIALLY_COMPLETED
    assert d.completion_type == "resource_limit_reached_with_results"
    assert d.qualified_record_count == 1


def test_hybrid_no_completed_work_is_failed():
    with pytest.raises(CompletionIncompleteError, match="INCOMPLETE_WITHOUT_COMPLETED_WORK"):
        CompletionDecisionService().decide(
            run=None,
            spec_payload=_spec("HYBRID", min_records=5),
            partition_counts={"passed": 0},
            eligible_url_count=4,
            terminal_url_count=0,
            fetched_page_count=0,
            record_count=0,
            batch_unique_counts=[],
            qualified_record_count=0,
            runtime_limit_reason=None,
            user_stopped=False,
            settings=ValidationSettings(),
            search_round_count=3,
            max_search_rounds=3,
        )


def test_exploratory_exhausted_search_rounds_with_results_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=5),
        partition_counts={"passed": 2},
        eligible_url_count=4,
        terminal_url_count=4,
        fetched_page_count=4,
        record_count=2,
        batch_unique_counts=[],
        qualified_record_count=2,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
        search_round_count=3,
        max_search_rounds=3,
    )
    assert d.outcome == CompletionOutcome.PARTIALLY_COMPLETED
    assert d.completion_type == "resource_limit_reached_with_results"


def test_completion_view_has_no_money_fields():
    """模块需求 51 / D-036：CompletionDecision 禁止任何金额预算字段。"""
    from app.validation.completion import CompletionDecisionView

    allowed = {
        "outcome",
        "status",
        "reason",
        "is_partial",
        "completion_type",
        "qualified_record_count",
        "saturation_evidence",
        "runtime_limit_reason",
        "scope_completion_metadata",
        "continue_hints",
    }
    assert set(CompletionDecisionView.model_fields.keys()) == allowed
