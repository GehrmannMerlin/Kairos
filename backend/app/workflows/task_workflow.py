"""M-07 TaskWorkflow: deterministic orchestration + collaborative stop (D-025).

Workflow 不做任何 DB/HTTP/LLM/文件副作用；全部放入 Activity。PostgreSQL 是业务
状态事实来源，Temporal History 是执行位置与恢复事实来源。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

    from app.activities.approval import (
        BlockHighRiskNodeInput,
        RequestApprovalInput,
        ResumeFromApprovalInput,
        block_high_risk_node,
        request_approval,
        resume_from_approval,
    )
    from app.activities.completion import (
        ResolveCompletionInput,
        ResolveCompletionResult,
        resolve_completion,
    )
    from app.activities.credential_approval import (
        ResolveCredentialAccessInput,
        resolve_credential_access,
    )
    from app.activities.discovery_approval import (
        ResolveRobotsOverrideInput,
        resolve_robots_override,
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
    from app.activities.reliability import (
        HeartbeatTaskSlotInput,
        RecordResourceWaitInput,
        heartbeat_task_slot,
        record_resource_wait,
    )
    from app.activities.replan import (
        ReplanContinuationInput,
        replan_for_continuation,
    )
    from app.activities.task_execution import (
        CommitCheckpointInput,
        CompleteRunInput,
        EnsureRunStartedInput,
        FailRunInput,
        MarkCancelledInput,
        MarkPartialInput,
        MarkPausedInput,
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        fail_run,
        mark_cancelled,
        mark_partial,
        mark_paused,
    )
    from app.reliability.pools import workflow_queue_override


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
        # M-11：EXTRACT 小批次重跑轮次（node_id → batch_round）。MORE_PENDING 后递增，
        # 下一轮 fetch 回填到 unit.batch_round，供 execute_safe_unit 区分 lifecycle attempt。
        self._extract_rounds: dict[str, int] = {}

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
        # 受控重规划循环状态（deterministic）：当前执行的 plan 版本与搜索轮次。
        # replan_for_continuation 返回新版本后推进；不引入 Date.now/random。
        self._current_plan_version = inp.plan_version
        self._search_round_count = 1
        # M-16 task admission（Level 1+2，D-071）：无全局/单用户 slot → 记录等待事实并
        # sleep 重试。任务保持 QUEUED，绝不以失败表达资源等待（§38 WAITING 非 FAILED）。
        while True:
            start_res = await workflow.execute_activity(
                ensure_run_started,
                EnsureRunStartedInput(
                    task_id=inp.task_id,
                    user_id=inp.user_id,
                    run_id=inp.run_id,
                    spec_version=inp.spec_version,
                    plan_version=self._current_plan_version,
                ),
                start_to_close_timeout=timedelta(seconds=60),
            )
            if start_res.started:
                break
            if start_res.waiting_reason:
                await workflow.execute_activity(
                    record_resource_wait,
                    RecordResourceWaitInput(
                        task_id=inp.task_id,
                        user_id=inp.user_id,
                        run_id=inp.run_id,
                        waiting_reason=start_res.waiting_reason or "task_limit",
                        retry_after_seconds=start_res.retry_after_seconds,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                )
            wait_seconds = start_res.retry_after_seconds if start_res.waiting_reason else 5.0
            await workflow.sleep(timedelta(seconds=wait_seconds))

        while True:
            try:
                # M-16：task slot heartbeat（延长资源 lease；资源占用事实，非业务 Checkpoint）。
                await workflow.execute_activity(
                    heartbeat_task_slot,
                    HeartbeatTaskSlotInput(
                        task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                )
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
                    # M-12 完成判定（D-006）：无更多单元时计算 CompletionDecision，区分
                    # 正常/部分/继续/失败。CONTINUE → 受控 replan → 复位 index 后继续执行。
                    completion: ResolveCompletionResult = await workflow.execute_activity(
                        resolve_completion,
                        ResolveCompletionInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            spec_version=inp.spec_version,
                            plan_version=self._current_plan_version,
                            search_round_count=self._search_round_count,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    if completion.outcome == "CONTINUE":
                        remaining = (completion.continue_hints or {}).get("remaining_search_rounds")
                        if remaining is not None and int(remaining) <= 0:
                            # 无剩余搜索轮次（decide 应已判 PARTIAL；此处为确定性兜底）。
                            await workflow.execute_activity(
                                mark_partial,
                                MarkPartialInput(
                                    task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                                ),
                                start_to_close_timeout=timedelta(seconds=60),
                            )
                            return TaskWorkflowResult(
                                inp.task_id, inp.run_id, "PARTIALLY_COMPLETED"
                            )
                        replan = await workflow.execute_activity(
                            replan_for_continuation,
                            ReplanContinuationInput(
                                task_id=inp.task_id,
                                user_id=inp.user_id,
                                run_id=inp.run_id,
                                spec_version=inp.spec_version,
                                current_plan_version=self._current_plan_version,
                                search_round_count=self._search_round_count,
                                continue_hints=completion.continue_hints or {},
                            ),
                            start_to_close_timeout=timedelta(seconds=180),
                        )
                        if replan.status != "OK" or replan.new_plan_version is None:
                            # 重规划失败但有 committed work（CONTINUE 仅在有 work 时出现）。
                            await workflow.execute_activity(
                                mark_partial,
                                MarkPartialInput(
                                    task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                                ),
                                start_to_close_timeout=timedelta(seconds=60),
                            )
                            return TaskWorkflowResult(
                                inp.task_id, inp.run_id, "PARTIALLY_COMPLETED"
                            )
                        self._current_plan_version = replan.new_plan_version
                        self._search_round_count += 1
                        self._last_index = 0
                        continue
                    if completion.status == "FAILED":
                        await workflow.execute_activity(
                            fail_run,
                            FailRunInput(
                                task_id=inp.task_id,
                                user_id=inp.user_id,
                                run_id=inp.run_id,
                                error_code=completion.failure_code or "EXECUTION_FAILED",
                            ),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        return TaskWorkflowResult(inp.task_id, inp.run_id, "FAILED")
                    if completion.partial:
                        await workflow.execute_activity(
                            mark_partial,
                            MarkPartialInput(
                                task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                            ),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        return TaskWorkflowResult(inp.task_id, inp.run_id, "PARTIALLY_COMPLETED")
                    await workflow.execute_activity(
                        complete_run,
                        CompleteRunInput(
                            task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    return TaskWorkflowResult(inp.task_id, inp.run_id, "COMPLETED")

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
                            plan_version=self._current_plan_version,
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

                # M-11：EXTRACT 小批次重跑 → 回填 batch_round（区分各批 lifecycle attempt）。
                if unit.node_type == "extract":
                    round_no = self._extract_rounds.get(unit.node_id or str(unit.index), 0)
                    if round_no:
                        unit = replace(unit, batch_round=round_no)

                # M-16：按 ResourceClass 确定性路由 execute_safe_unit 到对应 TaskQueue
                # （CORE → workflow 自身队列；HTTP/BROWSER/LLM_SEARCH → 固定常量）。
                # M-11：start_to_close 取 NodeDefinition.timeout_seconds（单一事实来源），
                # 避免长节点（extract/browser_render）被固定 120s 提前取消；
                # 有界 retry policy 防止与内层 provider 重试形成乘法，且取消永不重试。
                timeout = timedelta(seconds=unit.timeout_seconds or 120)
                exec_kwargs: dict = {
                    "start_to_close_timeout": timeout,
                    "retry_policy": RetryPolicy(
                        maximum_attempts=3,
                        initial_interval=timedelta(seconds=2),
                        non_retryable_error_types=["CancelledError", "asyncio.CancelledError"],
                    ),
                }
                queue_override = workflow_queue_override(unit.resource_class or "")
                if queue_override:
                    exec_kwargs["task_queue"] = queue_override
                exec_result: ExecuteUnitResult = await workflow.execute_activity(
                    execute_safe_unit,
                    ExecuteUnitInput(run_id=inp.run_id, unit=unit),
                    **exec_kwargs,
                )
                if exec_result.status == "MORE_PENDING":
                    # M-11 小批次：本批已提交，仍有剩余快照。提交本批 checkpoint 后不推进
                    # index，重取同一 EXTRACT 单元处理下一小批（与 RESOURCE_WAITING 同型）。
                    # 递增 batch_round：Temporal 新 activity 都报 attempt=1，必须显式区分。
                    node_key = unit.node_id or str(unit.index)
                    self._extract_rounds[node_key] = self._extract_rounds.get(node_key, 1) + 1
                    refs = exec_result.committed_refs or {}
                    await workflow.execute_activity(
                        commit_checkpoint,
                        CommitCheckpointInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            spec_version=inp.spec_version,
                            plan_version=self._current_plan_version,
                            batch_identity=str(refs.get("batch_identity") or f"unit-{unit.index}"),
                            node_run_id=None,
                            input_fingerprint=unit.input_fingerprint,
                            committed_refs=exec_result.committed_refs,
                            content_hash=None,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    continue
                if exec_result.status == "RESOURCE_WAITING":
                    # M-16：资源池无 slot → 等待，不推进 _last_index，不失败（D-071 §38）。
                    refs = exec_result.committed_refs or {}
                    await workflow.execute_activity(
                        record_resource_wait,
                        RecordResourceWaitInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            waiting_reason=str(refs.get("waiting_reason", "pool_limit")),
                            resource_class=str(refs.get("resource_class") or ""),
                            retry_after_seconds=float(refs.get("wait_seconds", 5.0)),
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    await workflow.sleep(timedelta(seconds=float(refs.get("wait_seconds", 5.0))))
                    continue  # 不推进 index，重取同一单元
                if exec_result.status in {"NODE_EXECUTOR_UNAVAILABLE", "FAILED"}:
                    # Runtime executor failures are terminal. They must not be
                    # checkpointed, advanced, blocked as approvals, or classified
                    # as partial completion.
                    error_code = exec_result.error_code or (
                        "NODE_EXECUTOR_UNAVAILABLE"
                        if exec_result.status == "NODE_EXECUTOR_UNAVAILABLE"
                        else "EXECUTION_FAILED"
                    )
                    await workflow.execute_activity(
                        fail_run,
                        FailRunInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            error_code=error_code,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    return TaskWorkflowResult(inp.task_id, inp.run_id, "FAILED")
                if exec_result.status == "WAITING_APPROVAL":
                    # M-09 robots override JIT 审批：executor 已创建 Approval（复用 M-08
                    # ApprovalService + outbox → approval_resolution Signal）。此处等待同一
                    # approval_id 的 Signal，然后 consume 复验 fingerprint 并迁移 Frontier。
                    refs = exec_result.committed_refs or {}
                    approval_id = refs.get("approval_id")
                    if approval_id is not None:
                        self._waiting_approval_id = int(approval_id)
                        self._latest_approval = None
                        try:
                            await workflow.wait_condition(
                                lambda: (
                                    self._latest_approval is not None
                                    and self._latest_approval.approval_id
                                    == self._waiting_approval_id
                                ),
                                timeout=timedelta(seconds=inp.pause_timeout_seconds),
                            )
                        except TimeoutError:
                            continue  # 仍等待，不失败
                        latest = self._latest_approval
                        decision = latest.decision.upper() if latest else ""
                        await workflow.execute_activity(
                            resolve_robots_override,
                            ResolveRobotsOverrideInput(
                                user_id=inp.user_id,
                                task_id=inp.task_id,
                                approval_id=int(approval_id),
                                url_hash=str(refs.get("url_hash", "")),
                                parameters=refs.get("parameters") or {},
                                decision=(
                                    decision if decision in ("APPROVED", "REJECTED") else "REJECTED"
                                ),
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                        )
                        await workflow.execute_activity(
                            commit_checkpoint,
                            CommitCheckpointInput(
                                task_id=inp.task_id,
                                user_id=inp.user_id,
                                run_id=inp.run_id,
                                spec_version=inp.spec_version,
                                plan_version=self._current_plan_version,
                                batch_identity=f"unit-{unit.index}",
                                node_run_id=None,
                                input_fingerprint=unit.input_fingerprint,
                                committed_refs=exec_result.committed_refs,
                                content_hash=None,
                            ),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        self._last_index = unit.index
                        continue
                if exec_result.status == "CREDENTIAL_REQUIRED":
                    # M-10 凭据访问：保存凭据 → credential_access Approval → 批准后
                    # resolve_credential_access consume + WAITING_CREDENTIAL → READY_FOR_FETCH。
                    # 不推进 _last_index → 重新执行同一 Fetch 节点完成凭据访问。
                    refs = exec_result.committed_refs or {}
                    self._latest_approval = None
                    try:
                        await workflow.wait_condition(
                            lambda: self._latest_approval is not None or self._cancel_requested,
                            timeout=timedelta(seconds=inp.pause_timeout_seconds),
                        )
                    except TimeoutError:
                        continue  # 用户未提供/未批准凭据前，节点保持当前 index，不失败
                    if self._cancel_requested:
                        continue  # 循环顶处理 cancel
                    latest = self._latest_approval
                    decision = latest.decision.upper() if latest else "REJECTED"
                    await workflow.execute_activity(
                        resolve_credential_access,
                        ResolveCredentialAccessInput(
                            user_id=inp.user_id,
                            task_id=inp.task_id,
                            approval_id=int(latest.approval_id) if latest else 0,
                            url_hash=str(refs.get("url_hash", "")),
                            parameters=refs.get("parameters") or {},
                            decision=(
                                decision if decision in ("APPROVED", "REJECTED") else "REJECTED"
                            ),
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    await workflow.execute_activity(
                        resume_from_approval,
                        ResumeFromApprovalInput(task_id=inp.task_id, user_id=inp.user_id),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    await workflow.execute_activity(
                        commit_checkpoint,
                        CommitCheckpointInput(
                            task_id=inp.task_id,
                            user_id=inp.user_id,
                            run_id=inp.run_id,
                            spec_version=inp.spec_version,
                            plan_version=self._current_plan_version,
                            batch_identity=f"unit-{unit.index}",
                            node_run_id=None,
                            input_fingerprint=unit.input_fingerprint,
                            committed_refs=exec_result.committed_refs,
                            content_hash=None,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    continue
                await workflow.execute_activity(
                    commit_checkpoint,
                    CommitCheckpointInput(
                        task_id=inp.task_id,
                        user_id=inp.user_id,
                        run_id=inp.run_id,
                        spec_version=inp.spec_version,
                        plan_version=self._current_plan_version,
                        batch_identity=str(
                            (exec_result.committed_refs or {}).get("batch_identity")
                            or f"unit-{unit.index}"
                        ),
                        node_run_id=None,
                        input_fingerprint=unit.input_fingerprint,
                        committed_refs=exec_result.committed_refs,
                        content_hash=None,
                    ),
                    start_to_close_timeout=timedelta(seconds=60),
                )
                # EXTRACT 单元完成（OK）：清理小批次轮次状态。
                if unit.node_type == "extract":
                    self._extract_rounds.pop(unit.node_id or str(unit.index), None)
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
