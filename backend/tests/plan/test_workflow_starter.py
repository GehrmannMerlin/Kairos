"""Workflow starter idempotency at the pre-created run boundary."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.workflows.starter import TaskWorkflowStarter
from temporalio.exceptions import WorkflowAlreadyStartedError


class _AlreadyStartedClient:
    def __init__(self, *, reported_workflow_id: str) -> None:
        self.reported_workflow_id = reported_workflow_id
        self.calls = 0

    async def start_workflow(self, *args, **kwargs):
        self.calls += 1
        raise WorkflowAlreadyStartedError(
            self.reported_workflow_id,
            "task_workflow",
            run_id="temporal-run-1",
        )


@pytest.mark.asyncio
async def test_matching_workflow_already_started_is_success() -> None:
    workflow_id = "task-workflow-42"
    client = _AlreadyStartedClient(reported_workflow_id=workflow_id)
    starter = TaskWorkflowStarter(client, Settings())

    result = await starter.start_persisted_run(
        user_id=7,
        task_id=42,
        run_id=99,
        spec_version=1,
        plan_version=2,
        workflow_id=workflow_id,
    )

    assert result.run_id == 99
    assert result.workflow_id == workflow_id
    assert client.calls == 1


@pytest.mark.asyncio
async def test_mismatched_workflow_already_started_is_not_hidden() -> None:
    client = _AlreadyStartedClient(reported_workflow_id="task-workflow-other")
    starter = TaskWorkflowStarter(client, Settings())

    with pytest.raises(WorkflowAlreadyStartedError):
        await starter.start_persisted_run(
            user_id=7,
            task_id=42,
            run_id=99,
            spec_version=1,
            plan_version=2,
            workflow_id="task-workflow-42",
        )
