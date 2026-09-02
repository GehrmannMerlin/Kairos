from __future__ import annotations

import json
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

    from app.activities import (
        CollectionContextInput,
        FinalizeCollectionInput,
        TaskRunUpdate,
        finalize_collection_run_activity,
        load_collection_context_activity,
        persist_agent_event_activity,
        update_task_run_activity,
    )
    from app.agent.deps import KairosAgentDeps
    from app.agent.runtime import kairos_agent
    from app.domain import (
        CollectionExecutionContext,
        EventEnvelope,
        TaskRunStatus,
        bounded_payload,
    )


class KairosAgentWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deps: KairosAgentDeps
    prompt: str = Field(min_length=1, max_length=20_000)


class KairosAgentWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_run_id: str
    answer: str = Field(max_length=20_000)


def _event(
    input_data: KairosAgentWorkflowInput, event_type: str, summary: str, payload: dict
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        task_id=input_data.deps.task_id,
        task_run_id=input_data.deps.task_run_id,
        owner_id=input_data.deps.user_id,
        agent_name="kairos-agent-v1",
        summary=summary,
        payload=bounded_payload(payload),
        occurred_at=workflow.now(),
    )


def _collection_prompt(user_prompt: str, context: CollectionExecutionContext) -> str:
    spec = {
        "spec_version_id": context.spec_version_id,
        "mode": context.mode.value,
        "goal": context.goal,
        "fields": [
            {
                "name": field.name,
                "type": field.type.value,
                "required": field.required,
                "description": (field.description or "")[:500],
            }
            for field in context.fields
        ],
        "sources": [
            {
                "source_id": source.source_id,
                "url": source.url,
                "status": source.status.value,
                "snapshot_id": source.snapshot_id,
            }
            for source in context.sources
        ],
    }
    return (
        "This is a Kairos SPECIFIED_SOURCE collection run. The confirmed CollectionSpec is an "
        "immutable business contract and the listed seed URLs are the complete authorized scope. "
        "For every unprocessed source, call fetch_source, inspect_snapshot in bounded chunks, extract "
        "only values present in the snapshot, and call commit_extraction. Evidence quotes must be "
        "verbatim text from the snapshot; never fabricate values or evidence. If there is no matching "
        "record, commit_extraction(records=[], source_complete=true). Call get_collection_progress "
        "before finishing; remaining_sources must be zero before you claim completion. Do not use "
        "generic fetch_url for this collection.\n\n"
        f"Collection context:\n{json.dumps(spec, ensure_ascii=False, sort_keys=True)}\n\n"
        f"User instruction:\n{user_prompt}"
    )


@workflow.defn
class KairosAgentWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [kairos_agent]

    @workflow.run
    async def run(self, input_data: KairosAgentWorkflowInput) -> KairosAgentWorkflowResult:
        await workflow.execute_activity(
            persist_agent_event_activity,
            args=[_event(input_data, "run.started", "Agent run started", {})],
            start_to_close_timeout=timedelta(seconds=15),
        )
        prompt = input_data.prompt
        collection_context: CollectionExecutionContext | None = None
        if input_data.deps.spec_version_id is not None:
            collection_context = await workflow.execute_activity(
                load_collection_context_activity,
                args=[
                    CollectionContextInput(
                        task_id=input_data.deps.task_id,
                        task_run_id=input_data.deps.task_run_id,
                        owner_id=input_data.deps.user_id,
                        spec_version_id=input_data.deps.spec_version_id,
                    )
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            await workflow.execute_activity(
                persist_agent_event_activity,
                args=[
                    _event(
                        input_data,
                        "collection.started",
                        "Specified-source collection started",
                        {
                            "spec_version_id": collection_context.spec_version_id,
                            "source_count": len(collection_context.sources),
                        },
                    )
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            prompt = _collection_prompt(prompt, collection_context)
        if input_data.deps.workspace_id is not None:
            prompt = (
                "A workspace is authorized for this run. Its permission is "
                f"{input_data.deps.workspace_permission.value}. Discover files with workspace tools; "
                "do not assume file contents.\n\nUser instruction:\n" + prompt
            )
        try:
            result = await kairos_agent.run(prompt, deps=input_data.deps)
            answer = str(result.output)[:20_000]
            if collection_context is not None:
                completion = await workflow.execute_activity(
                    finalize_collection_run_activity,
                    args=[
                        FinalizeCollectionInput(
                            task_id=input_data.deps.task_id,
                            task_run_id=input_data.deps.task_run_id,
                            owner_id=input_data.deps.user_id,
                            spec_version_id=collection_context.spec_version_id,
                            final_answer=answer,
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                completion_event_type = (
                    "collection.completed"
                    if completion.task_run_status is TaskRunStatus.COMPLETED
                    else "collection.partially_completed"
                    if completion.task_run_status is TaskRunStatus.PARTIALLY_COMPLETED
                    else "run.failed"
                )
                await workflow.execute_activity(
                    persist_agent_event_activity,
                    args=[
                        _event(
                            input_data,
                            completion_event_type,
                            completion.summary,
                            {
                                "remaining_sources": completion.remaining_sources,
                                "task_status": completion.task_status.value,
                                "task_run_status": completion.task_run_status.value,
                            },
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
            if collection_context is None or completion.task_run_status is not TaskRunStatus.FAILED:
                await workflow.execute_activity(
                    persist_agent_event_activity,
                    args=[
                        _event(
                            input_data,
                            "run.completed",
                            "Agent run completed",
                            {"answer_chars": len(answer)},
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
            if collection_context is None:
                await workflow.execute_activity(
                    update_task_run_activity,
                    args=[
                        TaskRunUpdate(
                            task_run_id=input_data.deps.task_run_id,
                            status=TaskRunStatus.COMPLETED,
                            final_answer=answer,
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
            return KairosAgentWorkflowResult(task_run_id=input_data.deps.task_run_id, answer=answer)
        except Exception as exc:
            error_type = type(exc).__name__
            await workflow.execute_activity(
                persist_agent_event_activity,
                args=[
                    _event(
                        input_data,
                        "run.failed",
                        "Agent run failed",
                        {"error_type": error_type},
                    )
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            await workflow.execute_activity(
                update_task_run_activity,
                args=[
                    TaskRunUpdate(
                        task_run_id=input_data.deps.task_run_id,
                        status=TaskRunStatus.FAILED,
                        error_message=error_type,
                    )
                ],
                start_to_close_timeout=timedelta(seconds=15),
            )
            raise
