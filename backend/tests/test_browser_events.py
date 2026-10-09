"""Phase 4 browser events + deterministic completion tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.browser.events import cap_action_events, emit_browser_event
from app.domain import (
    CollectionProgress,
    evaluate_collection_completion,
)
from app.repositories import has_agent_event, insert_task


def _progress(**overrides) -> CollectionProgress:
    base = {
        "total_sources": 4,
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


def test_action_events_capped_and_aggregated() -> None:
    actions = [{"action_type": "click", "short_description": f"step {i}"} for i in range(40)]
    capped = cap_action_events(actions)
    assert len(capped) == 31  # 30 + 1 aggregated
    assert capped[-1]["action_type"] == "aggregated"
    assert "10 more" in capped[-1]["short_description"]
    # Under the limit: no aggregation.
    small = cap_action_events(actions[:5])
    assert len(small) == 5


def test_completion_browser_required_budget_remaining_continues() -> None:
    progress = _progress(browser_required_sources=2, browser_tasks_remaining=3)
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=2,
        browser_tasks_remaining=3,
    )
    assert decision.decision == "CONTINUE"


def test_completion_browser_budget_exhausted_partial() -> None:
    progress = _progress(browser_required_sources=1, browser_tasks_remaining=0)
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
    # Browser blocked (no browser-required sources remain) and search budget remains.
    progress = _progress(browser_required_sources=0, browser_tasks_remaining=0)
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=0,
        browser_tasks_remaining=0,
    )
    assert decision.decision == "CONTINUE"


def test_completion_browser_success_target_reached() -> None:
    progress = _progress(passed_canonical_records=5, remaining_to_target=0)
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=0,
        browser_tasks_remaining=0,
    )
    assert decision.decision == "COMPLETED"
    assert decision.reason == "TARGET_REACHED"


@pytest.mark.asyncio
async def test_emit_browser_event_persists_and_is_owner_scoped() -> None:
    owner = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner)
    await emit_browser_event(
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner,
        event_type="browser.started",
        summary="Browser session started",
        payload={"source_id": "src-1", "attempt": 1},
    )
    assert await has_agent_event(task_id, run_id, owner, "browser.started") is True
    # A different owner does not see it.
    assert await has_agent_event(task_id, run_id, "other", "browser.started") is False