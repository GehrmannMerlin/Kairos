"""M-07 TaskWorkflow: deterministic orchestration + collaborative stop (D-025).

Workflow 不做任何 DB/HTTP/LLM/文件副作用；全部放入 Activity。PostgreSQL 是业务
状态事实来源，Temporal History 是执行位置与恢复事实来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.activities.execution_seam import (
        ExecuteUnitInput,
        ExecuteUnitResult,
        ExecutionUnit,
        FetchUnitInput,
        FetchUnitResult,
        execute_safe_unit,
        fetch_next_execution_unit,
    )
    from app.activities.task_execution import (
        CommitCheckpointInput,
        CompleteRunInput,
        EnsureRunStartedInput,
        MarkCancelledInput,
        MarkPausedInput,
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        mark_cancelled,
        mark_paused,
    )


@dataclass
class TaskWorkflowInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int = 0
    pause_timeout_seconds: int = 300
    cancel_timeout_seconds: int = 300


@dataclass
class TaskWorkflowResult:
    task_id: int
    run_id: int
    final_state: str


@dataclass
class ApprovalResolutionSignal:
    approval_id: int
    decision: str
    parameter_fingerprint: str
    spec_version: int


@dataclass
class SafePauseSignal:
    reason: str = "USER_RECONFIGURE"


@workflow.defn(name="task_workflow")
class TaskWorkflow:
    def __init__(self) -> None:
        self._pause_requested = False
        self._resume_requested = False
        self._cancel_requested = False
        self._last_index = 0
        self._latest_approval: ApprovalResolutionSignal | None = None

    @workflow.signal
    async def pause(self, reason: str | None = None) -> None:
        self._pause_requested = True

    @workflow.signal
    async def resume(self) -> None:
        self._resume_requested = True

    @workflow.signal
    async def cancel(self, reason: str | None = None) -> None:
        self._cancel_requested = True

    @workflow.signal
    async def approval_resolution(self, signal: ApprovalResolutionSignal) -> None:
        # M-08 消费；M-07 只接收，不实现审批业务逻辑。
        self._latest_approval = signal

    @workflow.signal
    async def safe_pause(self, signal: SafePauseSignal) -> None:
        # 用户改向前的安全暂停（D-025）；M-07 只建立契约。
        self._pause_requested = True

    @workflow.run
    async def run(self, inp: TaskWorkflowInput) -> TaskWorkflowResult:
        await workflow.execute_activity(
            ensure_run_started,
            EnsureRunStartedInput(
                task_id=inp.task_id,
                user_id=inp.user_id,
                run_id=inp.run_id,
                spec_version=inp.spec_version,
                plan_version=inp.plan_version,
            ),
            start_to_close_timeout=timedelta(seconds=60),
        )

        while True:
            if self._cancel_requested:
                await workflow.execute_activity(
                    mark_cancelled,
                    MarkCancelledInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
                    start_to_close_timeout=timedelta(seconds=60),
                )
                return TaskWorkflowResult(inp.task_id, inp.run_id, "CANCELLED")

            if self._pause_requested:
                # 协作式暂停：当前安全单元已由上一轮 commit；标记 PAUSED 后等待恢复。
                await workflow.execute_activity(
                    mark_paused,
                    MarkPausedInput(task_id=inp.task_id, user_id=inp.user_id),
                    start_to_close_timeout=timedelta(seconds=60),
                )
                self._pause_requested = False
                self._resume_requested = False
                await workflow.wait_condition(
                    lambda: self._resume_requested or self._cancel_requested,
                    timeout=timedelta(seconds=inp.pause_timeout_seconds),
                )
                continue

            fetch: FetchUnitResult = await workflow.execute_activity(
                fetch_next_execution_unit,
                FetchUnitInput(run_id=inp.run_id, after_index=self._last_index),
                start_to_close_timeout=timedelta(seconds=30),
            )
            unit: ExecutionUnit | None = fetch.unit
            if unit is None:
                break

            exec_result: ExecuteUnitResult = await workflow.execute_activity(
                execute_safe_unit,
                ExecuteUnitInput(run_id=inp.run_id, unit=unit),
                start_to_close_timeout=timedelta(seconds=120),
            )
            await workflow.execute_activity(
                commit_checkpoint,
                CommitCheckpointInput(
                    task_id=inp.task_id,
                    user_id=inp.user_id,
                    run_id=inp.run_id,
                    spec_version=inp.spec_version,
                    plan_version=inp.plan_version,
                    batch_identity=f"unit-{unit.index}",
                    node_run_id=None,
                    input_fingerprint=unit.input_fingerprint,
                    committed_refs=exec_result.committed_refs,
                    content_hash=None,
                ),
                start_to_close_timeout=timedelta(seconds=60),
            )
            self._last_index = unit.index

        await workflow.execute_activity(
            complete_run,
            CompleteRunInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
            start_to_close_timeout=timedelta(seconds=60),
        )
        return TaskWorkflowResult(inp.task_id, inp.run_id, "COMPLETED")
