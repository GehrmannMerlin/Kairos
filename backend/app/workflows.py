from __future__ import annotations

import json
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

    from app.activities import (
        ApplyCollectionCompletionDecisionInput,
        CollectionContextInput,
        EvaluateCollectionCompletionInput,
        FinalizeCollectionInput,
        TaskRunUpdate,
        apply_collection_completion_decision_activity,
        evaluate_collection_completion_activity,
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


MAX_AGENT_CONTINUATIONS = 3


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
                "origin": source.origin.value,
                "status": source.status.value,
                "snapshot_id": source.snapshot_id,
                "title": source.title,
                "snippet": source.snippet,
            }
            for source in context.sources
        ],
        "target_count": context.target_count,
        "scope_domains": context.scope_domains,
        "search_limits": context.search_limits.model_dump(mode="json"),
    }
    if context.mode.value == "SPECIFIED_SOURCE":
        instructions = (
            "This is a Kairos SPECIFIED_SOURCE collection run. The confirmed CollectionSpec is an "
            "immutable business contract and the listed seed URLs are the complete authorized scope. "
            "HTTP FIRST: for every unprocessed source, call fetch_source, then inspect_snapshot in "
            "bounded chunks, extract only values present in the snapshot, and call commit_extraction. "
            "Evidence quotes must be verbatim text from the snapshot; never fabricate values or evidence. "
            "If an HTTP snapshot has too little visible text, is an app shell, requires JavaScript, or "
            "hides the needed content behind expandable read-only UI, call request_browser_task(source_id) "
            "instead of fabricating or skipping; after it returns a browser snapshot, re-inspect that "
            "snapshot and then commit_extraction. Never request a browser for robots-blocked, private, "
            "403/401, 404, or captcha sources — treat those normally as BLOCKED/FAILED. If there is truly "
            "no matching record, commit_extraction(records=[], source_complete=true). Call "
            "get_collection_progress before finishing; remaining_sources must be zero before you claim "
            "completion. Do not use generic fetch_url for this collection."
        )
    else:
        instructions = (
            f"This is a Kairos {context.mode.value} collection run. The confirmed CollectionSpec is an "
            "immutable business contract. Start by calling get_collection_progress. If the target is not "
            "reached, use search_sources to design a query around the goal, required fields, missing "
            "information, and existing results. Do not repeat an identical query. HTTP FIRST: process "
            "actionable sources before adding more searches with fetch_source, inspect_snapshot in bounded "
            "chunks, and commit_extraction. If a snapshot lacks the needed rendered content (little "
            "visible text, app shell, JS-required, hidden behind expandable read-only UI), call "
            "request_browser_task(source_id) and then re-inspect the browser snapshot before committing. "
            "Never request a browser for robots-blocked, private, 403/401, 404, or captcha sources. "
            "Search snippet is not evidence; only PageSnapshot content can produce a Record or "
            "FieldEvidence. Call get_collection_progress after each batch. The deterministic system "
            "decides completion and saturation; never claim completion based only on final prose."
        )
    return (
        f"{instructions}\n\n"
        f"Collection context:\n{json.dumps(spec, ensure_ascii=False, sort_keys=True)}\n\n"
        f"User instruction:\n{user_prompt}"
    )


def _continuation_prompt(context: CollectionExecutionContext) -> str:
    return (
        f"Continue the {context.mode.value} Kairos collection using the same immutable CollectionSpec. "
        "Read get_collection_progress now. Prioritize actionable_sources; for each unprocessed source "
        "use fetch_source, inspect_snapshot in bounded chunks, and commit_extraction with only verified "
        "PageSnapshot evidence. If the target is still missing and search budget remains, choose a new "
        "search_sources query based on the missing fields. Search snippets are never evidence. Do not "
        "claim completion from prose; the system evaluator decides it."
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
                        f"{collection_context.mode.value} collection started",
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
            continuation_count = 0
            result = await kairos_agent.run(prompt, deps=input_data.deps)
            answer = str(result.output)[:20_000]
            completion = None
            if collection_context is not None and collection_context.mode.value != "SPECIFIED_SOURCE":
                decision = await workflow.execute_activity(
                    evaluate_collection_completion_activity,
                    args=[
                        EvaluateCollectionCompletionInput(
                            task_id=input_data.deps.task_id,
                            task_run_id=input_data.deps.task_run_id,
                            owner_id=input_data.deps.user_id,
                            spec_version_id=collection_context.spec_version_id,
                            agent_continuations=continuation_count,
                            max_agent_continuations=MAX_AGENT_CONTINUATIONS,
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                while decision.decision == "CONTINUE":
                    continuation_count += 1
                    prompt = _continuation_prompt(collection_context)
                    result = await kairos_agent.run(prompt, deps=input_data.deps)
                    answer = str(result.output)[:20_000]
                    decision = await workflow.execute_activity(
                        evaluate_collection_completion_activity,
                        args=[
                            EvaluateCollectionCompletionInput(
                                task_id=input_data.deps.task_id,
                                task_run_id=input_data.deps.task_run_id,
                                owner_id=input_data.deps.user_id,
                                spec_version_id=collection_context.spec_version_id,
                                agent_continuations=continuation_count,
                                max_agent_continuations=MAX_AGENT_CONTINUATIONS,
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                completion = await workflow.execute_activity(
                    apply_collection_completion_decision_activity,
                    args=[
                        ApplyCollectionCompletionDecisionInput(
                            task_id=input_data.deps.task_id,
                            task_run_id=input_data.deps.task_run_id,
                            owner_id=input_data.deps.user_id,
                            spec_version_id=collection_context.spec_version_id,
                            decision=decision,
                            final_answer=answer,
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
                completion_event_type = (
                    "collection.completed"
                    if decision.decision == "COMPLETED"
                    else "collection.partially_completed"
                    if decision.decision == "PARTIALLY_COMPLETED"
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
                                "decision": decision.decision,
                                "reason": decision.reason,
                                "remaining_sources": completion.remaining_sources,
                                "task_status": completion.task_status.value,
                                "task_run_status": completion.task_run_status.value,
                                "agent_continuations": continuation_count,
                            },
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=15),
                )
            elif collection_context is not None:
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
            if collection_context is None or (
                completion is not None and completion.task_run_status is not TaskRunStatus.FAILED
            ):
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
