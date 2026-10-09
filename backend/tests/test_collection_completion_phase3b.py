from __future__ import annotations

import pytest
from app.domain import (
    CollectionCompletionDecision,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionProgress,
    CollectionSpecConfirm,
    SearchRoundStatus,
    SearchRoundSummary,
    evaluate_collection_completion,
    evaluate_saturation,
)


def _progress(
    *,
    passed: int = 3,
    target: int = 5,
    rounds: int = 1,
    saturation: str = "NOT_REACHED",
    discovered: int = 3,
    processed: int = 3,
) -> CollectionProgress:
    return CollectionProgress(
        total_sources=discovered,
        pending_sources=max(discovered - processed, 0),
        fetched_sources=0,
        processed_sources=processed,
        failed_sources=0,
        blocked_sources=0,
        total_records=passed,
        passed_records=passed,
        needs_review_records=0,
        rejected_records=0,
        remaining_sources=max(discovered - processed, 0),
        mode=CollectionMode.EXPLORATORY,
        target_count=target,
        passed_canonical_records=passed,
        observations_total=passed,
        canonical_records_total=passed,
        remaining_to_target=max(target - passed, 0),
        search_rounds_completed=rounds,
        max_search_rounds=5,
        sources_discovered=discovered,
        saturation_state=saturation,
    )


def _round(number: int, new_passed: int) -> SearchRoundSummary:
    return SearchRoundSummary(
        search_round_id=f"round-{number}",
        task_run_id="run",
        round_number=number,
        query="query",
        query_hash=f"hash-{number}",
        provider="fake",
        requested_results=10,
        returned_results=1,
        accepted_results=1,
        new_sources=1,
        passed_records_before=0,
        passed_records_after=new_passed,
        new_passed_records=new_passed,
        status=SearchRoundStatus.COMPLETED,
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def test_saturation_requires_two_completed_zero_progress_rounds() -> None:
    assert evaluate_saturation([_round(1, 1), _round(2, 0)]) == "NOT_REACHED"
    assert evaluate_saturation([_round(1, 0), _round(2, 0)]) == "SATURATED"


@pytest.mark.parametrize(
    ("progress", "expected_decision", "expected_reason"),
    [
        (_progress(passed=5), "COMPLETED", "TARGET_REACHED"),
        (_progress(saturation="SATURATED"), "PARTIALLY_COMPLETED", "SEARCH_SATURATED"),
        (_progress(rounds=5), "PARTIALLY_COMPLETED", "SEARCH_ROUND_LIMIT"),
        (_progress(), "CONTINUE", "SEARCH_BUDGET_REMAINS"),
    ],
)
def test_completion_is_deterministic(
    progress: CollectionProgress, expected_decision: str, expected_reason: str
) -> None:
    decision = evaluate_collection_completion(
        progress,
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
    )

    assert decision.decision == expected_decision
    assert decision.reason == expected_reason


def test_completion_limits_and_technical_failure_are_explicit() -> None:
    discovered = evaluate_collection_completion(
        _progress(discovered=50, processed=2),
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
    )
    processed = evaluate_collection_completion(
        _progress(discovered=30, processed=30),
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
    )
    capped = evaluate_collection_completion(
        _progress(),
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=3,
        max_agent_continuations=3,
    )
    failed = evaluate_collection_completion(
        _progress(),
        max_discovered_sources=50,
        max_processed_sources=30,
        agent_continuations=0,
        max_agent_continuations=3,
        technical_failure=True,
    )

    assert discovered.reason == "DISCOVERED_SOURCE_LIMIT"
    assert processed.reason == "PROCESSED_SOURCE_LIMIT"
    assert capped.reason == "AGENT_CONTINUATION_LIMIT"
    assert failed.decision == "FAILED"
    assert failed.reason == "TECHNICAL_FAILURE"


@pytest.mark.asyncio
async def test_target_completion_skips_search_candidates_but_keeps_hybrid_seed() -> None:
    from uuid import uuid4

    from app.repositories import (
        apply_collection_completion_decision,
        confirm_collection_spec,
        create_search_round,
        create_task_run,
        get_collection_spec,
        insert_task,
        list_collection_sources,
        persist_search_round_results,
    )
    from app.search import NormalizedSearchResult

    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="find projects",
            fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com/seed"],
            scope_domains=["example.com"],
            target_count=1,
            mode=CollectionMode.HYBRID,
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "find projects")
    round_row = await create_search_round(
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec.spec_version_id,
        query="projects",
        query_hash=uuid4().hex,
        provider="fake",
        requested_results=1,
    )
    await persist_search_round_results(
        search_round_id=round_row.search_round_id,
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec.spec_version_id,
        results=[
            NormalizedSearchResult(
                url="https://example.com/discovered",
                title="Discovered",
                snippet="discovered",
                rank=1,
            )
        ],
        returned_results=1,
    )

    await apply_collection_completion_decision(
        task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec.spec_version_id,
        decision=CollectionCompletionDecision(
            decision="COMPLETED",
            reason="TARGET_REACHED",
            passed_records=1,
            target_count=1,
            search_rounds=1,
            saturation_state="NOT_REACHED",
        ),
        final_answer="done",
    )
    sources = await list_collection_sources(task_id, owner_id, spec.spec_version_id)

    assert [(source.origin, source.status, source.failure_code) for source in sources] == [
        ("SEED", "PENDING", None),
        ("SEARCH", "SKIPPED", "TARGET_REACHED"),
    ]
