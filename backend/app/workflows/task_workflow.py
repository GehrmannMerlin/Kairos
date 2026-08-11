"""M-07 TaskWorkflow: deterministic orchestration + collaborative stop (D-025).

Workflow 不做任何 DB/HTTP/LLM/文件副作用；全部放入 Activity。PostgreSQL 是业务
状态事实来源，Temporal History 是执行位置与恢复事实来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.activities.approval import (
        BlockHighRiskNodeInput,
        RequestApprovalInput,
        ResumeFromApprovalInput,
        block_high_risk_node,
        request_approval,
        resume_from_approval,
    )
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
        FailRunInput,
        MarkCancelledInput,
        MarkPausedInput,
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        fail_run,
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
        self._waiting_approval_id: int | None = None

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
            try:
                if self._cancel_requested:
                    await workflow.execute_activity(
                        mark_cancelled,
                        MarkCancelledInput(
                            task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                        ),
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
                    try:
                        await workflow.wait_condition(
                            lambda: self._resume_requested or self._cancel_requested,
                            timeout=timedelta(seconds=inp.pause_timeout_seconds),
                        )
                    except TimeoutError:
                        # pause_timeout 是复检间隔而非硬截止：用户未在窗口内恢复时，
                        # 任务保持 PAUSED，重新进入暂停等待。绝不能落入下方 broad
                        # except 触发 fail_run——那会把暂停任务写成矛盾终态
                        # (task=PAUSED + run=failed)。cancel 仍会在下一轮循环顶优先处理。
                        self._pause_requested = True
                    continue

                fetch: FetchUnitResult = await workflow.execute_activity(
                    fetch_next_execution_unit,
                    FetchUnitInput(run_id=inp.run_id, after_index=self._last_index),
                    start_to_close_timeout=timedelta(seconds=30),
                )
                unit: ExecutionUnit | None = fetch.unit
                if unit is None:
                    break

                if unit.requires_approval:
                    # JIT 审批（D-017 / 三十三）：Workflow 到达高风险 Node 才 request_approval，
                    # 任务进入 WAITING_APPROVAL，等待 M-07 approval_resolution Signal。
                    req = await workflow.execute_activity(
                        request_approval,
                        RequestApprovalInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            spec_version=inp.spec_version,
                            plan_version=inp.plan_version,
                            unit=unit,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    self._waiting_approval_id = req.approval_id
                    self._latest_approval = None
                    try:
                        await workflow.wait_condition(
                            lambda: (
                                self._latest_approval is not None
                                and self._latest_approval.approval_id == self._waiting_approval_id
                            ),
                            timeout=timedelta(seconds=inp.pause_timeout_seconds),
                        )
                    except TimeoutError:
                        # 等待超时：任务保持 WAITING_APPROVAL，下一轮循环继续等待
                        # （与 pause 语义一致，不进入 fail_run）。
                        continue
                    latest = self._latest_approval
                    decision = latest.decision.upper() if latest else ""
                    if decision != "APPROVED":
                        # Reject/Revoke/Expired：block 高风险 Node，绝不执行（三十五）。
                        await workflow.execute_activity(
                            block_high_risk_node,
                            BlockHighRiskNodeInput(
                                task_id=inp.task_id,
                                user_id=inp.user_id,
                                run_id=inp.run_id,
                                node_id=unit.node_id,
                            ),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        self._last_index = unit.index
                        continue
                    # 批准后先回到 RUNNING（WAITING_APPROVAL → RUNNING），再继续执行
                    await workflow.execute_activity(
                        resume_from_approval,
                        ResumeFromApprovalInput(task_id=inp.task_id, user_id=inp.user_id),
                        start_to_close_timeout=timedelta(seconds=60),
                    )

                exec_result: ExecuteUnitResult = await workflow.execute_activity(
                    execute_safe_unit,
                    ExecuteUnitInput(run_id=inp.run_id, unit=unit),
                    start_to_close_timeout=timedelta(seconds=120),
                )
                if exec_result.status == "NODE_EXECUTOR_UNAVAILABLE":
                    # 生产运行时 M-09+ 尚未实现该 Node Activity：稳定错误，不冒充能力（四十七）。
                    await workflow.execute_activity(
                        block_high_risk_node,
                        BlockHighRiskNodeInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            node_id=unit.node_id,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    self._last_index = unit.index
                    continue
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
            except Exception:
                # 执行循环出现不可恢复错误：fail_run 收尾（任务 FAILED、Run failed）。
                # ensure_run_started 保持在工作流启动段，其 non-retryable 业务错误应作为
                # 工作流失败暴露，而不是被这里吞掉转成 FAILED 过渡。
                await workflow.execute_activity(
                    fail_run,
                    FailRunInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
                    start_to_close_timeout=timedelta(seconds=60),
                )
                return TaskWorkflowResult(inp.task_id, inp.run_id, "FAILED")

        await workflow.execute_activity(
            complete_run,
            CompleteRunInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
            start_to_close_timeout=timedelta(seconds=60),
        )
        return TaskWorkflowResult(inp.task_id, inp.run_id, "COMPLETED")
