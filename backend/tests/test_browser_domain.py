"""Phase 4 browser domain model and deterministic completion tests."""

from __future__ import annotations

import pytest
from app.domain import (
    BrowserLimits,
    BrowserTask,
    BrowserTaskReason,
    BrowserTaskStatus,
    CollectionCompletionDecision,
    CollectionProgress,
    CollectionSourceStatus,
    evaluate_collection_completion,
)
from pydantic import ValidationError


def _progress(**overrides) -> CollectionProgress:
    base = {
        "total_sources": 3,
        "pending_sources": 1,
        "fetched_sources": 1,
        "processed_sources": 1,
        "failed_sources": 0,
        "blocked_sources": 0,
        "total_records": 2,
        "passed_records": 1,
        "needs_review_records": 0,
        "rejected_records": 0,
        "remaining_sources": 2,
        "mode": "EXPLORATORY",
        "target_count": 5,
        "passed_canonical_records": 1,
        "observations_total": 2,
        "canonical_records_total": 1,
        "remaining_to_target": 4,
        "search_rounds_completed": 3,
        "max_search_rounds": 10,
        "sources_discovered": 4,
        "saturation_state": "NOT_REACHED",
    }
    base.update(overrides)
    return CollectionProgress(**base)


def test_browser_limits_defaults() -> None:
    limits = BrowserLimits()
    assert limits.max_browser_tasks_per_run == 5
    assert limits.max_steps_per_task == 20
    assert limits.task_timeout_seconds == 120
    assert limits.max_navigation_count == 5
    assert limits.max_action_events == 30


def test_browser_limits_hard_caps_reject_out_of_range() -> None:
    with pytest.raises(ValidationError):
        BrowserLimits(max_browser_tasks_per_run=99)
    with pytest.raises(ValidationError):
        BrowserLimits(max_steps_per_task=0)
    with pytest.raises(ValidationError):
        BrowserLimits(task_timeout_seconds=1000)


def test_browser_task_defaults() -> None:
    task = BrowserTask(
        browser_task_id="bt-1",
        owner_id="u1",
        task_id="t1",
        task_run_id="r1",
        spec_version_id="s1",
        source_id="src-1",
        policy_version="browser-policy-v1",
        created_at=__import__("datetime").datetime(2026, 9, 3),
        updated_at=__import__("datetime").datetime(2026, 9, 3),
    )
    assert task.status is BrowserTaskStatus.PENDING
    assert task.attempt_count == 0
    assert task.snapshot_id is None
    assert task.failure_code is None


def test_browser_reason_enum() -> None:
    assert BrowserTaskReason.JS_RENDER_REQUIRED.value == "JS_RENDER_REQUIRED"
    assert BrowserTaskReason.CONTENT_HIDDEN.value == "CONTENT_HIDDEN"
    assert BrowserTaskReason.EXPAND_REQUIRED.value == "EXPAND_REQUIRED"


def test_source_browser_required_status() -> None:
    assert CollectionSourceStatus.BROWSER_REQUIRED.value == "BROWSER_REQUIRED"


def test_completion_browser_budget_remaining_continues() -> None:
    progress = _progress()
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=1,
        browser_tasks_remaining=4,
    )
    assert decision.decision == "CONTINUE"


def test_completion_browser_budget_exhausted_partial() -> None:
    progress = _progress()
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=1,
        browser_tasks_remaining=0,
    )
    assert decision.decision == "PARTIALLY_COMPLETED"
    assert decision.reason == "BROWSER_TASK_LIMIT"


def test_completion_browser_blocked_search_budget_continues() -> None:
    progress = _progress()
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=0,
        browser_tasks_remaining=0,
    )
    # No browser-required sources and search budget remains -> CONTINUE.
    assert decision.decision == "CONTINUE"


def test_completion_browser_success_target_reached() -> None:
    progress = _progress(passed_canonical_records=5, remaining_to_target=0)
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=1,
        browser_tasks_remaining=2,
    )
    assert decision.decision == "COMPLETED"
    assert decision.reason == "TARGET_REACHED"


def test_progress_browser_fields_default_zero() -> None:
    progress = _progress()
    assert progress.browser_required_sources == 0
    assert progress.browser_tasks_used == 0
    assert progress.browser_tasks_remaining == 0
    assert progress.browser_completed == 0
    assert progress.browser_blocked == 0
    assert progress.browser_failed == 0
    assert progress.actionable_browser_sources == []


def test_decision_schema_stable() -> None:
    decision = CollectionCompletionDecision(
        decision="CONTINUE",
        reason="SEARCH_BUDGET_REMAINS",
        passed_records=1,
        target_count=5,
        search_rounds=3,
        saturation_state="NOT_REACHED",
    )
    assert decision.remaining_runtime_budget is None
