from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

    from app.activities import TaskRunUpdate, persist_agent_event_activity, update_task_run_activity
    from app.agent.deps import KairosAgentDeps
    from app.agent.runtime import kairos_agent
    from app.domain import EventEnvelope, TaskRunStatus, bounded_payload


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
        if input_data.deps.workspace_id is not None:
            prompt = (
                "A workspace is authorized for this run. Its permission is "
                f"{input_data.deps.workspace_permission.value}. Discover files with workspace tools; "
                "do not assume file contents.\n\nUser instruction:\n" + prompt
            )
        try:
            result = await kairos_agent.run(prompt, deps=input_data.deps)
            answer = str(result.output)[:20_000]
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
