from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from temporalio import activity

from app.domain import (
    CollectionCompletionDecision,
    CollectionExecutionContext,
    CompletionResult,
    EventEnvelope,
    TaskRunStatus,
    bounded_payload,
    evaluate_collection_completion,
)
from app.repositories import (
    apply_collection_completion_decision,
    finalize_collection_run,
    get_collection_context,
    get_collection_progress,
    insert_agent_event,
    update_task_run,
)


class TaskRunUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_run_id: str
    status: TaskRunStatus
    final_answer: str | None = None
    error_message: str | None = None


class CollectionContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_run_id: str
    owner_id: str
    spec_version_id: str


class FinalizeCollectionInput(CollectionContextInput):
    final_answer: str | None = None


class EvaluateCollectionCompletionInput(CollectionContextInput):
    agent_continuations: int = 0
    max_agent_continuations: int = 3
    technical_failure: bool = False


class ApplyCollectionCompletionDecisionInput(CollectionContextInput):
    decision: CollectionCompletionDecision
    final_answer: str | None = None


@activity.defn(name="kairos.persist_agent_event")
async def persist_agent_event_activity(event: EventEnvelope) -> None:
    bounded = event.model_copy(update={"payload": bounded_payload(event.payload)})
    await insert_agent_event(bounded)


@activity.defn(name="kairos.update_task_run")
async def update_task_run_activity(update: TaskRunUpdate) -> None:
    await update_task_run(
        update.task_run_id,
        update.status,
        final_answer=update.final_answer,
        error_message=update.error_message,
    )


@activity.defn(name="kairos.load_collection_context")
async def load_collection_context_activity(
    input_data: CollectionContextInput,
) -> CollectionExecutionContext:
    context = await get_collection_context(
        input_data.task_id,
        input_data.task_run_id,
        input_data.owner_id,
        input_data.spec_version_id,
    )
    if context is None:
        raise RuntimeError("COLLECTION_CONTEXT_NOT_FOUND")
    return context


@activity.defn(name="kairos.finalize_collection_run")
async def finalize_collection_run_activity(input_data: FinalizeCollectionInput) -> CompletionResult:
    return await finalize_collection_run(
        input_data.task_id,
        input_data.task_run_id,
        input_data.owner_id,
        input_data.spec_version_id,
        final_answer=input_data.final_answer,
    )


@activity.defn(name="kairos.evaluate_collection_completion")
async def evaluate_collection_completion_activity(
    input_data: EvaluateCollectionCompletionInput,
) -> CollectionCompletionDecision:
    context = await get_collection_context(
        input_data.task_id,
        input_data.task_run_id,
        input_data.owner_id,
        input_data.spec_version_id,
    )
    progress = await get_collection_progress(
        input_data.task_id,
        input_data.task_run_id,
        input_data.owner_id,
        input_data.spec_version_id,
    )
    if context is None:
        raise RuntimeError("COLLECTION_CONTEXT_NOT_FOUND")
    return evaluate_collection_completion(
        progress,
        max_discovered_sources=context.search_limits.max_discovered_sources,
        max_processed_sources=context.search_limits.max_processed_sources,
        agent_continuations=input_data.agent_continuations,
        max_agent_continuations=input_data.max_agent_continuations,
        browser_required_sources=progress.browser_required_sources,
        browser_tasks_remaining=progress.browser_tasks_remaining,
        technical_failure=input_data.technical_failure,
    )


@activity.defn(name="kairos.apply_collection_completion_decision")
async def apply_collection_completion_decision_activity(
    input_data: ApplyCollectionCompletionDecisionInput,
) -> CompletionResult:
    return await apply_collection_completion_decision(
        input_data.task_id,
        task_run_id=input_data.task_run_id,
        owner_id=input_data.owner_id,
        spec_version_id=input_data.spec_version_id,
        decision=input_data.decision,
        final_answer=input_data.final_answer,
    )
