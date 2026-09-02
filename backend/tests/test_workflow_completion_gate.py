from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.activities import (
    ApplyCollectionCompletionDecisionInput,
    EvaluateCollectionCompletionInput,
    apply_collection_completion_decision_activity,
    evaluate_collection_completion_activity,
    load_collection_context_activity,
    persist_agent_event_activity,
)
from app.agent.deps import KairosAgentDeps
from app.domain import (
    CollectionCompletionDecision,
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSourceOrigin,
    CollectionSourceStatus,
    CollectionSourceSummary,
    CompletionResult,
    SearchLimits,
    TaskRunStatus,
    TaskStatus,
    WorkspacePermission,
)
from app.workflows import KairosAgentWorkflow, KairosAgentWorkflowInput


@pytest.mark.asyncio
async def test_exploratory_workflow_reenters_same_agent_after_continue_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    owner_id = f"owner-{uuid4().hex}"
    spec_id = f"spec-{uuid4().hex}"
    context = CollectionExecutionContext(
        spec_version_id=spec_id,
        mode=CollectionMode.EXPLORATORY,
        goal="Find projects",
        fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
        sources=[
            CollectionSourceSummary(
                source_id="source-1",
                url="https://example.com",
                canonical_url="https://example.com",
                origin=CollectionSourceOrigin.SEARCH,
                status=CollectionSourceStatus.PENDING,
            )
        ],
        target_count=1,
        search_limits=SearchLimits(),
    )
    decisions = iter(
        [
            CollectionCompletionDecision(
                decision="CONTINUE",
                reason="SEARCH_BUDGET_REMAINS",
                passed_records=0,
                target_count=1,
                search_rounds=1,
                saturation_state="NOT_REACHED",
            ),
            CollectionCompletionDecision(
                decision="COMPLETED",
                reason="TARGET_REACHED",
                passed_records=1,
                target_count=1,
                search_rounds=1,
                saturation_state="NOT_REACHED",
            ),
        ]
    )
    prompts: list[str] = []
    deps_seen: list[KairosAgentDeps] = []

    async def fake_agent_run(prompt: str, *, deps: KairosAgentDeps) -> SimpleNamespace:
        prompts.append(prompt)
        deps_seen.append(deps)
        return SimpleNamespace(output="I am finished")

    async def fake_execute_activity(activity: object, *, args: list[object], **kwargs: object) -> object:
        del kwargs
        name = getattr(activity, "__name__", "")
        if name == load_collection_context_activity.__name__:
            return context
        if name == evaluate_collection_completion_activity.__name__:
            input_data = args[0]
            assert isinstance(input_data, EvaluateCollectionCompletionInput)
            return next(decisions)
        if name == apply_collection_completion_decision_activity.__name__:
            input_data = args[0]
            assert isinstance(input_data, ApplyCollectionCompletionDecisionInput)
            return CompletionResult(
                task_run_id=input_data.task_run_id,
                task_status=TaskStatus.COMPLETED,
                task_run_status=TaskRunStatus.COMPLETED,
                remaining_sources=0,
                summary="collection finished with target_reached",
            )
        if name == persist_agent_event_activity.__name__:
            return None
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr("app.workflows.workflow.execute_activity", fake_execute_activity)
    monkeypatch.setattr("app.workflows.workflow.now", lambda: datetime.now(UTC))
    monkeypatch.setattr("app.workflows.kairos_agent.run", fake_agent_run)

    result = await KairosAgentWorkflow().run(
        KairosAgentWorkflowInput(
            deps=KairosAgentDeps(
                user_id=owner_id,
                task_id=task_id,
                task_run_id=run_id,
                spec_version_id=spec_id,
                model_config_id="test-model",
                workspace_permission=WorkspacePermission.NONE,
            ),
            prompt="find one project",
        )
    )

    assert result.answer == "I am finished"
    assert len(prompts) == 2
    assert prompts[0] != prompts[1]
    assert "Continue" in prompts[1]
    assert deps_seen[0] is deps_seen[1]
