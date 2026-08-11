"""CORE TEST F — Completion（模块需求 74）：定向范围完成 / 探索饱和 / 部分完成 / 无金额条件。"""

from __future__ import annotations

from app.validation.completion import CompletionDecisionService, SaturationTracker
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
        batch_unique_counts=[],
        qualified_record_count=0,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "PARTIALLY_COMPLETED"
    assert d.is_partial is True
    assert d.completion_type == "access_limited"


def test_exploratory_min_records_and_saturation_is_normal():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=3),
        partition_counts={"passed": 4},
        eligible_url_count=10,
        terminal_url_count=10,
        batch_unique_counts=[0, 0, 0],  # 最近 3 batch 新增 unique 率 0 → 饱和
        qualified_record_count=4,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "NORMAL_COMPLETED"
    assert d.completion_type == "exploratory_saturation"
    assert d.saturation_evidence["saturated"] is True


def test_exploratory_not_saturated_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=1),
        partition_counts={"passed": 1},
        eligible_url_count=10,
        terminal_url_count=5,
        batch_unique_counts=[3, 2, 1],  # 新增 unique 率 1~3 > 0 → 未饱和
        qualified_record_count=1,
        runtime_limit_reason=None,
        user_stopped=False,
        settings=ValidationSettings(),
    )
    assert d.status == "PARTIALLY_COMPLETED"


def test_runtime_limit_is_partial():
    d = CompletionDecisionService().decide(
        run=None,
        spec_payload=_spec("EXPLORATORY", min_records=10),
        partition_counts={"passed": 2},
        eligible_url_count=10,
        terminal_url_count=4,
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


def test_completion_view_has_no_money_fields():
    """模块需求 51 / D-036：CompletionDecision 禁止任何金额预算字段。"""
    from app.validation.completion import CompletionDecisionView

    allowed = {
        "status",
        "reason",
        "is_partial",
        "completion_type",
        "qualified_record_count",
        "saturation_evidence",
        "runtime_limit_reason",
        "scope_completion_metadata",
    }
    assert set(CompletionDecisionView.model_fields.keys()) == allowed
