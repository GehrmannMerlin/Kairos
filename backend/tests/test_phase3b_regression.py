"""Phase 3 regression: normal HTTP collection must NOT invoke the browser path.

Phase 4 adds an escalation path, but it must never change the Phase 3 outcome
for ordinary HTTP pages. When no source needs a browser, the deterministic
completion evaluator behaves exactly as Phase 3B did, and the durable agent's
toolset never surfaces request_browser_task for a plain static source.
"""

from __future__ import annotations

from app.agent.runtime import kairos_agent
from app.domain import (
    CollectionProgress,
    evaluate_collection_completion,
)


def _progress(**overrides) -> CollectionProgress:
    base = {
        "total_sources": 2,
        "pending_sources": 0,
        "fetched_sources": 0,
        "processed_sources": 2,
        "failed_sources": 0,
        "blocked_sources": 0,
        "total_records": 3,
        "passed_records": 3,
        "needs_review_records": 0,
        "rejected_records": 0,
        "remaining_sources": 0,
        "mode": "SPECIFIED_SOURCE",
        "target_count": 3,
        "passed_canonical_records": 3,
        "observations_total": 3,
        "canonical_records_total": 3,
        "remaining_to_target": 0,
        "search_rounds_completed": 0,
        "max_search_rounds": 0,
        "sources_discovered": 0,
        "saturation_state": "NOT_REACHED",
    }
    base.update(overrides)
    return CollectionProgress(**base)


def test_completion_without_browser_matches_phase3b_semantics() -> None:
    """With browser_required_sources=0, target reached -> COMPLETED, exactly Phase 3B."""
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
    assert decision.decision == "COMPLETED"
    assert decision.reason == "TARGET_REACHED"
    # The browser budget fields must not contribute when no browser is required.
    assert progress.browser_required_sources == 0
    assert progress.browser_tasks_used == 0


def test_completion_continues_phase3b_when_no_browser_and_budget_remains() -> None:
    """Target not reached, no browser required, search budget remains -> CONTINUE (unchanged)."""
    progress = _progress(passed_canonical_records=1, remaining_to_target=2)
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
    # Search saturation still decides when it should.
    progress2 = _progress(passed_canonical_records=1, saturation_state="SATURATED")
    decision2 = evaluate_collection_completion(
        progress2,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        browser_required_sources=0,
        browser_tasks_remaining=0,
    )
    assert decision2.decision == "PARTIALLY_COMPLETED"
    assert decision2.reason == "SEARCH_SATURATED"


def test_durable_agent_still_exposes_search_but_browser_tool_is_isolation_only() -> None:
    """The durable agent keeps the Phase 3 toolsets and gains one browser tool ONLY.

    The browser toolset exposes exactly request_browser_task; the Phase 3
    collection/search toolsets are untouched. No Playwright capability is mounted
    on the durable agent (P4-A), so a normal HTTP source can never transitively
    open a browser unless the model explicitly requests one.
    """
    toolset_ids = {
        getattr(ts, "id", None)  # noqa: B009 - toolset types are heterogeneous
        for ts in kairos_agent.toolsets
        if hasattr(ts, "id") and getattr(ts, "id") is not None  # noqa: B009
    }
    assert "kairos-collection-v1" in toolset_ids
    assert "kairos-search-v1" in toolset_ids
    assert "kairos-web-v1" in toolset_ids
    assert "kairos-browser-v1" in toolset_ids

    from app.agent.browser_tool import browser_toolset

    assert set(browser_toolset.tools) == {"request_browser_task"}


def test_collection_toolset_has_no_browser_tools() -> None:
    """The collection toolset only has fetch/inspect/commit/progress — never a browser tool."""
    from app.agent.runtime import _collection_toolset

    names = set(_collection_toolset.tools)
    assert names == {"fetch_source", "inspect_snapshot", "commit_extraction", "get_collection_progress"}
    assert "request_browser_task" not in names