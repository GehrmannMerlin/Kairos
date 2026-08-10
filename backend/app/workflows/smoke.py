"""M-01 smoke workflow.

Deterministic: it performs no network/LLM/file side effects itself. All side
effects are delegated to ``write_smoke_record`` Activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.activities.smoke import SmokeActivityInput, SmokeActivityResult, write_smoke_record


@dataclass
class SmokeWorkflowInput:
    message: str


@dataclass
class SmokeWorkflowResult:
    record_id: int
    object_key: str
    message: str


@workflow.defn(name="smoke_workflow")
class SmokeWorkflow:
    @workflow.run
    async def run(self, inp: SmokeWorkflowInput) -> SmokeWorkflowResult:
        result: SmokeActivityResult = await workflow.execute_activity(
            write_smoke_record,
            SmokeActivityInput(message=inp.message),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return SmokeWorkflowResult(
            record_id=result.record_id,
            object_key=result.object_key,
            message=result.message,
        )
