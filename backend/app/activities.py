from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from temporalio import activity

from app.domain import EventEnvelope, TaskRunStatus, bounded_payload
from app.repositories import insert_agent_event, update_task_run


class TaskRunUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_run_id: str
    status: TaskRunStatus
    final_answer: str | None = None
    error_message: str | None = None


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
