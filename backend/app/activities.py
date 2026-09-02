from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from temporalio import activity

from app.domain import (
    CollectionExecutionContext,
    CompletionResult,
    EventEnvelope,
    TaskRunStatus,
    bounded_payload,
)
from app.repositories import (
    finalize_collection_run,
    get_collection_context,
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
