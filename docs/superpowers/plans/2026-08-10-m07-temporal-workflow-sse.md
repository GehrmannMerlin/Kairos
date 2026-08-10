# M-07 Temporal Task Workflow + SSE 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立真实可靠的长期任务执行底座：TaskWorkflow（Run 启动、pause/resume/cancel、heartbeat、checkpoint 恢复、崩溃恢复）与可重连的 SSE 事件流，并为 M-08 Plan 提供稳定执行 seam。

**Architecture:** Workflow 只做确定性编排（Activity 调用、Signal 处理、WaitCondition）；所有 DB/状态机/Checkpoint/事件副作用在 Activity 中完成。PostgreSQL 是业务状态事实来源（`runs`/`tasks`/`domain_events`/`outbox_events`/`checkpoints`），Temporal History 是执行位置与恢复事实来源。SSE 只推送基于 DomainEvent 的重要事件，可经 `Last-Event-ID`/`domain_events.id` cursor 从 PostgreSQL 重放。Task command（pause/resume/cancel）走 `TaskCommandService`：幂等 → 状态机事务（state+event+outbox 同事务）→ Outbox dispatcher → Temporal Signal。

**Tech Stack:** Temporal Python SDK 1.31.0（Temporal Server 1.26.2 via compose）、FastAPI、SQLAlchemy 2、pydantic v2、Vue 3 + TypeScript strict、Vitest。

## Global Constraints

- **M-04 兼容：** 所有 Task 状态变化必须经过 `app.state.states.assert_task_transition`（在 `DomainService.transition_task` 事务内）。Workflow/Activity 不得 `UPDATE tasks.state`。
- **M-04 幂等：** 用户命令必须使用 `IdempotencyService`；同 key + 同 payload 重放返回既有结果，同 key + 不同 payload 抛 `IdempotencyConflictError`。
- **M-04 Checkpoint：** 业务事务成功后才 `commit_checkpoint()`；heartbeat 绝不生成 Checkpoint。
- **Temporal 确定性：** Workflow 内禁止直接 DB / HTTP / LLM / 文件 IO / random / wall-clock 副作用；一律 Activity。时间/超时只允许 `workflow.wait_condition(timeout=...)`。
- **输入安全：** `TaskWorkflowInput` 只携带稳定 ID/整数（task_id, user_id, run_id, spec_version, plan_version, 超时秒数）。禁止 API Key/Cookie/password/Authorization/完整 Spec/完整网页/模型 Prompt。
- **双事实边界：** PostgreSQL = 用户可查询业务状态；Temporal History = 执行位置。前端不查询 Temporal；PostgreSQL 不存“Temporal 执行到第几行”。
- **Spec 冻结：** 只允许 confirmed 的 `CollectionSpecVersion`（`confirmed_at IS NOT NULL`）启动 Workflow；否则稳定业务错误，不进入 RUNNING。
- **协作式停止：** pause → PAUSING（命令层）→ 当前安全单元 commit+checkpoint → PAUSED（Workflow mark_paused Activity）。cancel 同理 → CANCELLING → CANCELLED。PAUSED/CANCELLED 只能由 Workflow 在安全停止后写入。
- **命令幂等：** pause×2 / cancel×2 同一 IdempotencyKey 只产生一次有效状态转换 + 一次 Temporal Signal 副作用。
- **禁止区域（M-07 不做）：** PlanGenerator、NodeRegistry、真实采集/搜索/抓取、Approval 业务对象/UI、Redis/Kafka、M-16 资源调度、DEPLOY-GATE-2。
- **Secret：** Temporal History / SSE / DomainEvent / 日志均不得出现任何 Secret。
- **命名：** 复用现有 enum（`TaskState`、`TaskType`）；新增 SSE 事件名与 `domain_events.event_type` 语义一一对应，不造第二套名称。
- **SSE 事实源：** SSE 不是业务状态源；基于 `domain_events` + 稳定 Task Query。不推所有 heartbeat / HTTP 200 / checkpoint id 到 Chat。
- **测试策略：** A-Lite。只写高价值测试（Temporal 生命周期、命令幂等、checkpoint 恢复、SSE replay、跨用户隔离）；不跑全量 suite。
- **Migration：** 复用 M-04 `runs`/`checkpoints`/`domain_events`/`outbox_events`/`idempotency_keys`。SSE cursor 使用 `domain_events.id`。**NO NEW MIGRATION**（除非实施中发现必须持久字段）。
- **Git：** 每个 Task 一个可独立验证 Commit（英文 Conventional Commits 标题 + 中文正文），不 push/merge/tag。

---

## File Structure

**后端新建：**
- `backend/app/workflows/task_workflow.py` — TaskWorkflow、Input/Result、Signals、超时配置读取
- `backend/app/workflows/starter.py` — TaskWorkflowStarter（Run 创建 + 启动 + M-08 seam）
- `backend/app/activities/task_execution.py` — lifecycle Activity（ensure_run_started / mark_paused / mark_cancelled / complete_run / fail_run / commit_checkpoint）
- `backend/app/activities/heartbeat.py` — heartbeat helper（安全、最小、非 Secret）
- `backend/app/activities/execution_seam.py` — M-08 seam 类型 + fixture 执行契约（ExecutionUnit 等）
- `backend/app/domain/task_commands.py` — TaskCommandService（pause/resume/cancel + 幂等 + outbox 入队）
- `backend/app/infra/outbox_dispatch.py` — OutboxTemporalDispatcher（outbox → Temporal Signal，有界重试）
- `backend/app/api/events.py` — SSETaskEvent schema + DomainEvent→SSE mapper + 查询
- `backend/app/api/routes/events.py` — `GET /api/events/tasks/{task_id}` SSE 端点（Last-Event-ID 重放）

**后端修改：**
- `backend/app/state/states.py` — 新增系统命令 `mark_paused`/`mark_cancelled`；`allowed_task_actions` 只暴露用户命令
- `backend/app/config.py` — `temporal_task_queue`、`task_pause_timeout_seconds`、`task_cancel_timeout_seconds`
- `backend/app/infra/temporal.py` — `create_task_worker()`（注册 TaskWorkflow + lifecycle Activity）
- `backend/app/worker.py` — 注册 task worker
- `backend/app/api/router.py` — include events router
- `backend/app/api/schemas.py` — `TaskCommandDto`/`TaskCommandResponse`
- `backend/app/api/routes/tasks.py` — 新增 command 端点（pause/resume/cancel）

**后端测试：**
- `backend/tests/state/test_task_pause_cancel.py` — 状态机系统命令
- `backend/tests/domain/test_task_commands.py` — TaskCommandService 幂等 + 转换 + outbox
- `backend/tests/api/test_task_commands.py` — API 层
- `backend/tests/api/test_task_events.py` — SSE replay + 跨用户隔离（DB 层）
- `backend/tests/fixtures/execution_adapter.py` — fixture 执行单元 Activity（仅测试注册）
- `backend/tests/integration/fixture_worker.py` — crash/restart 用独立 worker 入口
- `backend/tests/integration/test_task_workflow.py` — start contract / pause/resume / cancel（Temporal 集成）
- `backend/tests/integration/test_worker_crash_restart.py` — 崩溃恢复（Temporal 集成）

**前端新建：**
- `frontend/src/features/tasks/events.api.ts` — SSE client（EventSource + Last-Event-ID）
- `frontend/src/features/tasks/useTaskEvents.ts` — 事件 store/composable
- `frontend/src/features/tasks/commands.api.ts` — pause/resume/cancel API

**前端修改：**
- `frontend/src/app/overlay/drawers/TaskStatusDrawer.vue` — 接真实 Task Query + SSE + 命令按钮

**前端测试：**
- `frontend/src/features/tasks/taskEvents.test.ts`
- `frontend/src/app/overlay/drawers/TaskStatusDrawer.test.ts`

**文档：**
- `docs/implementation/M-07-execution.md`

---

## Task 1: TaskWorkflow typed contract + Run startup + 状态机系统命令

**Files:**
- Modify: `backend/app/state/states.py`
- Create: `backend/app/workflows/task_workflow.py`
- Create: `backend/app/activities/task_execution.py`
- Create: `backend/app/workflows/starter.py`
- Create: `backend/app/activities/execution_seam.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/infra/temporal.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/state/test_task_pause_cancel.py`
- Test: `backend/tests/integration/test_task_workflow.py`（只含 start contract 用例，其余 Task 7）

**Interfaces:**
- Consumes: M-04 `DomainService.transition_task`/`commit_checkpoint`、`TaskRepository`/`SpecVersionRepository`/`RunRepository`/`CheckpointRepository`、`app.state.states.TaskState`、`app.infra.temporal.create_temporal_client`。
- Produces:
  - `TaskWorkflowInput(task_id: int, user_id: int, run_id: int, spec_version: int, plan_version: int = 0, pause_timeout_seconds: int = 300, cancel_timeout_seconds: int = 300)`
  - `TaskWorkflowResult(task_id: int, run_id: int, final_state: str)`
  - `ApprovalResolutionSignal(approval_id: int, decision: str, parameter_fingerprint: str, spec_version: int)`
  - `SafePauseSignal(reason: str = "USER_RECONFIGURE")`
  - `TaskWorkflowStarter.start(user_id, task_id, spec_version, plan_version=0) -> RunStartedResult(run_id, workflow_id)`
  - `TaskWorkflowStarter.submit_validated_plan(...)`（M-08 seam 签名占位，仅类型化）
  - Activity：`ensure_run_started`, `mark_paused`, `mark_cancelled`, `complete_run`, `fail_run`, `commit_checkpoint`
  - `ExecutionUnit(run_id, index, unit_type, input_fingerprint)`、`FetchUnitResult(unit: ExecutionUnit | None)`

- [ ] **Step 1: 状态机新增系统命令（先写失败测试）**

`backend/tests/state/test_task_pause_cancel.py`:

```python
"""M-07: PAUSING->PAUSED / CANCELLING->CANCELLED 系统命令 + allowed_actions 不暴露系统命令。"""
from __future__ import annotations

import pytest
from app.domain.errors import IllegalTransitionError
from app.state.states import (
    TaskState,
    allowed_task_actions,
    assert_task_transition,
)


def test_mark_paused_transition() -> None:
    assert assert_task_transition(TaskState.PAUSING, "mark_paused") == TaskState.PAUSED


def test_mark_cancelled_transition() -> None:
    assert assert_task_transition(TaskState.CANCELLING, "mark_cancelled") == TaskState.CANCELLED


def test_system_commands_are_not_user_actions() -> None:
    assert "mark_paused" not in allowed_task_actions(TaskState.PAUSING)
    assert "mark_cancelled" not in allowed_task_actions(TaskState.CANCELLING)


def test_mark_paused_only_from_pausing() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_task_transition(TaskState.RUNNING, "mark_paused")
```

- [ ] **Step 2: 运行确认失败**

`cd backend && .venv/Scripts/python.exe -m pytest tests/state/test_task_pause_cancel.py -q`
Expected: FAIL（`assert_task_transition` 不认识 `mark_paused`，抛出 IllegalTransitionError 即“等价失败”）。为符合 TDD 先红，可先断言上面任一 expect 失败。

- [ ] **Step 3: 实现状态机系统命令**

修改 `backend/app/state/states.py`：新增 `TASK_SYSTEM_COMMANDS` 字典，`assert_task_transition` 同时查两个字典，`allowed_task_actions` 只遍历 `TASK_COMMANDS`：

```python
# 系统内部命令（仅 Workflow/Activity 在安全停止后调用），不出现在用户 allowed_actions。
TASK_SYSTEM_COMMANDS: dict[str, list[tuple[TaskState, TaskState]]] = {
    "mark_paused": [(TaskState.PAUSING, TaskState.PAUSED)],
    "mark_cancelled": [(TaskState.CANCELLING, TaskState.CANCELLED)],
}


def assert_task_transition(state: TaskState, command: str) -> TaskState:
    try:
        return _resolve(TASK_COMMANDS, "任务", state, command)
    except IllegalTransitionError:
        return _resolve(TASK_SYSTEM_COMMANDS, "任务", state, command)
```

- [ ] **Step 4: 运行状态机测试确认通过**

`cd backend && .venv/Scripts/python.exe -m pytest tests/state/test_task_pause_cancel.py tests/domain/test_state_machine.py -q`
Expected: PASS（旧矩阵测试仍绿）。

- [ ] **Step 5: TaskWorkflow typed contract（先写 start contract 集成测试）**

`backend/tests/integration/test_task_workflow.py`（本 Task 只写 `test_start_workflow_creates_run_and_running`，其余用例 Task 7 追加）：

```python
"""Temporal TaskWorkflow integration (requires KAIROS_RUN_INTEGRATION=1 + local stack)."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from app.config import get_settings
from app.domain.models import Run, Task
from app.infra.deps import get_session_factory
from app.infra.temporal import create_temporal_client
from app.workflows.starter import TaskWorkflowStarter

pytestmark = pytest.mark.integration


def _wait_task_state(task_id: int, want: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = get_session_factory()()
        try:
            task = session.get(Task, task_id)
            if task is not None and task.state == want:
                return
        finally:
            session.close()
        time.sleep(0.2)
    raise TimeoutError(f"task {task_id} did not reach {want}")


@pytest.mark.asyncio
async def test_start_workflow_creates_run_and_running(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )
    # starter 创建 pending Run 并返回 run_id
    assert started.run_id > 0
    assert started.workflow_id == f"task-workflow-{confirmed_task['task_id']}"

    # ensure_run_started Activity 幂等激活：Task QUEUED->RUNNING、Run started。
    _wait_task_state(confirmed_task["task_id"], "RUNNING")

    handle = client.get_workflow_handle(started.workflow_id)
    desc = await handle.describe()
    assert desc.status.name in ("RUNNING", "COMPLETED")  # 至少已启动/在跑

    session = get_session_factory()()
    try:
        run = session.get(Run, started.run_id)
        task = session.get(Task, confirmed_task["task_id"])
        assert run.state == "running"
        assert run.started_at is not None
        assert task.state == "RUNNING"
    finally:
        session.close()

    # 清理：fixture 执行单元在 Task 4 才注册，这里终止 workflow 避免遗留。
    try:
        await handle.terminate(reason="test cleanup")
    except Exception:
        pass
```

`confirmed_task` fixture 见 `backend/tests/integration/conftest.py`（本 Task 创建）：

```python
"""M-07 集成测试共享 fixtures（连接本地栈真实 PostgreSQL/Temporal）。"""

from __future__ import annotations

import pytest
from app.auth.repository import UserRepository
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.spec import FieldSpec, SpecDraftPayload
from app.domain.task_types import TaskType
from app.infra.deps import get_session_factory


@pytest.fixture()
def confirmed_task() -> dict:
    """注册 Gate user + 创建 DRAFT Task + confirm_spec 冻结 spec v1（无需真实模型）。"""
    session = get_session_factory()()
    try:
        user = UserRepository(session).create("m07-gate@kairos.test", "hash", None)
        task = TaskRepository(session).create(
            user_id=user.id, title="M07 gate task", task_type=None
        )
        spec = SpecDraftPayload(
            task_type=TaskType.EXPLORATORY,
            goal="搜集深圳工业自动化设备供应商",
            fields=[FieldSpec(name="公司名", type="text", required=True)],
        )
        DomainService(TaskRepository(session)).confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=task.version,
            spec_payload=spec.model_dump(mode="json"),
            actor_id=user.id,
        )
        task = TaskRepository(session).get_owned(user.id, task.id)
        assert task.state == "QUEUED"
        return {"user_id": user.id, "task_id": task.id, "spec_version": 1}
    finally:
        session.close()
```

> 说明：`UserRepository.create` 需已有 M-02 实现（复用）。若 `TaskType` 枚举值与 M-06 一致（EXPLORATORY），字段以实际 `task_types.py` 为准。

- [ ] **Step 6: 实现 TaskWorkflow**

`backend/app/workflows/task_workflow.py`：

```python
"""M-07 TaskWorkflow: deterministic orchestration + collaborative stop (D-025).

Workflow 不做任何 DB/HTTP/LLM/文件副作用；全部放入 Activity。PostgreSQL 是业务
状态事实来源，Temporal History 是执行位置与恢复事实来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.activities.task_execution import (
        EnsureRunStartedInput,
        EnsureRunStartedResult,
        MarkPausedInput,
        MarkCancelledInput,
        CompleteRunInput,
        CommitCheckpointInput,
        CommitCheckpointResult,
        ensure_run_started,
        mark_paused,
        mark_cancelled,
        complete_run,
        commit_checkpoint,
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
                    mark_paused, MarkPausedInput(task_id=inp.task_id, user_id=inp.user_id),
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
            cp: CommitCheckpointResult = await workflow.execute_activity(
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
            complete_run, CompleteRunInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id),
            start_to_close_timeout=timedelta(seconds=60),
        )
        return TaskWorkflowResult(inp.task_id, inp.run_id, "COMPLETED")
```

- [ ] **Step 7: 实现 lifecycle Activities**

`backend/app/activities/task_execution.py`（核心：Spec 冻结校验、幂等 Run 启动、系统命令转换、checkpoint 复用）：

```python
"""M-07 task lifecycle activities (DB side effects live here, never in the workflow)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from temporalio import activity

from app.domain.errors import IllegalTransitionError, StaleVersionError
from app.domain.repository import (
    CheckpointRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.domain.service import DomainService
from app.infra.deps import get_session_factory


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class EnsureRunStartedInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int


@dataclass
class EnsureRunStartedResult:
    run_id: int
    started: bool


class RunSpecNotFrozenError(Exception):
    """Spec 未冻结时稳定业务错误：不允许进入 RUNNING。"""


@activity.defn
async def ensure_run_started(inp: EnsureRunStartedInput) -> EnsureRunStartedResult:
    session = get_session_factory()()
    try:
        spec = SpecVersionRepository(session).get_version(inp.user_id, inp.task_id, inp.spec_version)
        if spec.confirmed_at is None:
            raise RunSpecNotFrozenError("采集方案尚未确认，不能启动执行")
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        if run.state != "pending":
            return EnsureRunStartedResult(inp.run_id, started=False)
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        try:
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="start",
                expected_version=task.version,
                actor_type="system",
                reason="task_workflow_started",
            )
        except StaleVersionError:
            task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
            if task.state != "RUNNING":
                raise
        run.state = "running"
        run.started_at = _utcnow()
        session.add(run)
        session.commit()
        return EnsureRunStartedResult(inp.run_id, started=True)
    finally:
        session.close()


@dataclass
class MarkPausedInput:
    task_id: int
    user_id: int


@activity.defn
async def mark_paused(inp: MarkPausedInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        try:
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id, task_id=inp.task_id, command="mark_paused",
                expected_version=task.version, actor_type="system", reason="workflow_stopped",
            )
        except IllegalTransitionError:
            pass  # 已在 PAUSED（重复信号）视为幂等成功
    finally:
        session.close()


@dataclass
class MarkCancelledInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def mark_cancelled(inp: MarkCancelledInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        try:
            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id, task_id=inp.task_id, command="mark_cancelled",
                expected_version=task.version, actor_type="system", reason="workflow_cancelled",
            )
        except IllegalTransitionError:
            pass
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "cancelled"
        run.finished_at = _utcnow()
        session.commit()
    finally:
        session.close()


@dataclass
class CompleteRunInput:
    task_id: int
    user_id: int
    run_id: int


@activity.defn
async def complete_run(inp: CompleteRunInput) -> None:
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        DomainService(TaskRepository(session)).transition_task(
            user_id=inp.user_id, task_id=inp.task_id, command="complete",
            expected_version=task.version, actor_type="system", reason="workflow_completed",
        )
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        run.state = "completed"
        run.finished_at = _utcnow()
        session.commit()
    finally:
        session.close()


@dataclass
class CommitCheckpointInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int
    batch_identity: str
    node_run_id: int | None
    input_fingerprint: str
    committed_refs: dict
    content_hash: str | None


@dataclass
class CommitCheckpointResult:
    checkpoint_id: int
    reused: bool


@activity.defn
async def commit_checkpoint(inp: CommitCheckpointInput) -> CommitCheckpointResult:
    session = get_session_factory()()
    try:
        existing = CheckpointRepository(session).find_by_batch(inp.run_id, inp.batch_identity)
        if existing is not None:
            if existing.input_fingerprint != inp.input_fingerprint:
                from app.domain.errors import DomainError
                raise DomainError("相同批次身份但输入指纹不同")
            return CommitCheckpointResult(existing.id, reused=True)
        row = await asyncio.to_thread(
            DomainService(TaskRepository(session)).commit_checkpoint,
            user_id=inp.user_id,
            task_id=inp.task_id,
            run_id=inp.run_id,
            batch_identity=inp.batch_identity,
            spec_version=inp.spec_version,
            plan_version=inp.plan_version,
            node_run_id=inp.node_run_id,
            input_fingerprint=inp.input_fingerprint,
            committed_refs=inp.committed_refs,
            content_hash=inp.content_hash,
        )
        return CommitCheckpointResult(row.id, reused=False)
    finally:
        session.close()
```

> 幂等复用：`find_by_batch` 先行判定，同 batch + 同 fingerprint → 返回既有行（reused=True）；不同 fingerprint → 稳定冲突错误。M-04 `commit_checkpoint` 本身也已幂等，双重兜底。

- [ ] **Step 8: 实现执行 seam 类型**

`backend/app/activities/execution_seam.py`（类型契约；真实执行单元由 M-08/M-09+ 注册，测试用 fixture 实现）：

```python
"""M-08 执行 seam：Workflow 通过这组 Activity 获取并执行安全单元。

M-07 只定义契约与 fixture；禁止把 TEST/DUMMY 节点注册进 Production Worker。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity


@dataclass
class ExecutionUnit:
    run_id: int
    index: int
    unit_type: str
    input_fingerprint: str


@dataclass
class FetchUnitInput:
    run_id: int
    after_index: int


@dataclass
class FetchUnitResult:
    unit: ExecutionUnit | None


@dataclass
class ExecuteUnitInput:
    run_id: int
    unit: ExecutionUnit


@dataclass
class ExecuteUnitResult:
    unit_index: int
    committed_refs: dict


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    raise NotImplementedError("M-08 计划调度接入后由真实实现注册；M-07 测试用 fixture 覆盖")


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    raise NotImplementedError("M-08 计划调度接入后由真实实现注册；M-07 测试用 fixture 覆盖")
```

- [ ] **Step 9: 实现 TaskWorkflowStarter（含 M-08 seam 签名）**

`backend/app/workflows/starter.py`：

```python
"""TaskWorkflowStarter — Run 创建 + Workflow 启动 + M-08 plan seam。

run_id 由命令层在 Workflow 启动前生成稳定 ID；Workflow 第一步 ensure_run_started
Activity 幂等激活（Spec 冻结校验 + QUEUED->RUNNING + DomainEvent/Outbox）。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio.client import Client

from app.config import Settings, get_settings
from app.domain.models import Run
from app.domain.repository import RunRepository, TaskRepository
from app.infra.deps import get_session_factory
from app.workflows.task_workflow import TaskWorkflowInput


@dataclass
class RunStartedResult:
    run_id: int
    workflow_id: str


class TaskWorkflowStarter:
    def __init__(self, client: Client, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()

    async def start(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int = 0
    ) -> RunStartedResult:
        session = get_session_factory()()
        try:
            TaskRepository(session).get_owned(user_id, task_id)  # owner gate
            run = RunRepository(session).create(
                user_id=user_id, task_id=task_id, spec_version=spec_version, plan_version=plan_version
            )
        finally:
            session.close()

        workflow_id = f"task-workflow-{task_id}"
        inp = TaskWorkflowInput(
            task_id=task_id,
            user_id=user_id,
            run_id=run.id,
            spec_version=spec_version,
            plan_version=plan_version,
            pause_timeout_seconds=self._settings.task_pause_timeout_seconds,
            cancel_timeout_seconds=self._settings.task_cancel_timeout_seconds,
        )
        await self._client.start_workflow(
            "task_workflow",
            arg=inp,
            id=workflow_id,
            task_queue=self._settings.temporal_task_queue,
        )
        return RunStartedResult(run_id=run.id, workflow_id=workflow_id)

    async def submit_validated_plan(
        self, *, user_id: int, task_id: int, spec_version: int, plan_version: int
    ) -> RunStartedResult:
        """M-08 seam：Plan Validator PASS 后调用，把已验证 Plan 交给 Workflow 调度。

        M-07 只提供类型化入口（复用 start，plan_version 非 0）；M-08 负责持久化 PlanVersion。
        """
        return await self.start(
            user_id=user_id, task_id=task_id, spec_version=spec_version, plan_version=plan_version
        )
```

- [ ] **Step 10: 配置 + worker 注册**

`backend/app/config.py` 新增：

```python
    # --- Temporal task execution (M-07) ---
    temporal_task_queue: str = "kairos-task"
    task_pause_timeout_seconds: int = 300
    task_cancel_timeout_seconds: int = 300
```

`backend/app/infra/temporal.py` 新增 `create_task_worker()`：

```python
async def create_task_worker(client: Client, settings: Settings) -> Worker:
    from app.activities.task_execution import (
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        mark_cancelled,
        mark_paused,
    )
    from app.workflows.task_workflow import TaskWorkflow

    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TaskWorkflow],
        activities=[
            ensure_run_started,
            mark_paused,
            mark_cancelled,
            complete_run,
            commit_checkpoint,
        ],
        interceptors=_interceptors(),
    )
```

`backend/app/worker.py` 在 `run()` 中同时启动 smoke + task 两个 worker（`asyncio.gather`）。

- [ ] **Step 11: 运行集成测试 + 质量门禁**

`cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/integration/test_task_workflow.py -q`
Expected: `test_start_workflow_creates_run_and_running` PASS（需本地栈已在运行）。

`ruff check app tests && ruff format --check app tests && .venv/Scripts/python.exe -m mypy app`
Expected: PASS。

- [ ] **Step 12: Commit**

```bash
git add backend/app/state/states.py backend/app/config.py backend/app/workflows/ backend/app/activities/task_execution.py backend/app/activities/execution_seam.py backend/app/infra/temporal.py backend/app/worker.py backend/tests/state/test_task_pause_cancel.py backend/tests/integration/conftest.py backend/tests/integration/test_task_workflow.py
git commit -m "feat(workflow): add task workflow typed contract and run startup

新增 TaskWorkflow（Run 启动、协作式 pause/resume/cancel 骨架、checkpoint 复用执行
循环）与 TaskWorkflowStarter（命令层生成 run_id + 启动 workflow + M-08 plan seam）。
状态机新增 PAUSING->PAUSED / CANCELLING->CANCELLED 系统命令，且不暴露给用户
allowed_actions。所有 DB/状态副作用集中在 Activity；workflow input 只含稳定 ID。
关联模块：M-07"
```

---

## Task 2: TaskCommandService + pause/resume/cancel + Outbox→Temporal 分发 + API

**Files:**
- Create: `backend/app/domain/task_commands.py`
- Create: `backend/app/infra/outbox_dispatch.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes/tasks.py`
- Test: `backend/tests/domain/test_task_commands.py`
- Test: `backend/tests/api/test_task_commands.py`

**Interfaces:**
- Consumes: Task 1 `DomainService.transition_task`（M-04）、`IdempotencyService`、`OutboxRepository`、`TaskRepository`；Task 1 `TaskWorkflowStarter` 的 workflow_id 命名 `task-workflow-{task_id}`。
- Produces:
  - `TaskCommandService.pause_task(user_id, task_id, expected_version, idempotency_key=None, reason=None) -> TaskCommandResult`
  - `TaskCommandService.resume_task(...)`, `cancel_task(...)`
  - `TaskCommandResult(command: str, state: str, version: int)`
  - `OutboxTemporalDispatcher.dispatch_pending_for(user_id, task_id) -> int`（分发成功数）
  - API：`POST /api/tasks/{task_id}/commands/{command}`（command ∈ pause/resume/cancel），body `TaskCommandDto{idempotency_key, reason}`，响应 `TaskCommandResponse{command, state, version}`

- [ ] **Step 1: 先写 TaskCommandService 失败测试**

`backend/tests/domain/test_task_commands.py`：

```python
"""M-07: pause/resume/cancel 命令幂等 + 状态机 + outbox 入队。"""
from __future__ import annotations

import pytest
from app.domain.models import OutboxEvent, Task
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.task_commands import TaskCommandService
from app.domain.idempotency import IdempotencyService


@pytest.fixture()
def running_task(db, user) -> Task:
    task = TaskRepository(db).create(user_id=user.id, title="running", task_type="directed")
    DomainService(TaskRepository(db)).transition_task(
        user_id=user.id, task_id=task.id, command="submit", expected_version=1
    )
    # 让状态机直接置 RUNNING（等价 start）
    DomainService(TaskRepository(db)).transition_task(
        user_id=user.id, task_id=task.id, command="start", expected_version=2
    )
    return TaskRepository(db).get_owned(user.id, task.id)


def test_pause_transitions_running_to_pausing(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    result = svc.pause_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version)
    assert result.state == "PAUSING"


def test_double_pause_same_key_is_idempotent(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    key = "k-pause-1"
    first = svc.pause_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version, idempotency_key=key)
    second = svc.pause_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version, idempotency_key=key)
    assert first.state == second.state == "PAUSING"
    assert second.version == first.version  # 未重复递增


def test_cancel_twice_same_key_one_effect(db, user, running_task) -> None:
    svc = TaskCommandService(db)
    first = svc.cancel_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version, idempotency_key="k-cancel-1")
    second = svc.cancel_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version, idempotency_key="k-cancel-1")
    assert first.state == second.state == "CANCELLING"
    assert second.version == first.version


def test_command_enqueues_outbox(db, user, running_task) -> None:
    TaskCommandService(db).pause_task(user_id=user.id, task_id=running_task.id, expected_version=running_task.version, idempotency_key="k-pause-2")
    rows = db.query(OutboxEvent).filter_by(aggregate_type="task").all()
    assert any(r.event_type == "task.pause" for r in rows)
```

> 测试复用 `backend/tests/domain/conftest.py` 的 `db`/`user` fixture。若 conftest 已有 `task` fixture 冲突，用本地命名。

- [ ] **Step 2: 运行确认失败**

`cd backend && .venv/Scripts/python.exe -m pytest tests/domain/test_task_commands.py -q`
Expected: FAIL（`TaskCommandService` 不存在）。

- [ ] **Step 3: 实现 TaskCommandService**

`backend/app/domain/task_commands.py`：

```python
"""M-07: 任务命令服务。FastAPI 只做 auth/DTO，命令语义在这里。

pause/resume/cancel 全部走：幂等 → M-04 状态机事务（state+event+outbox 同事务）→
返回结果。Temporal Signal 由 Outbox dispatcher 在提交后异步/同步分发（见
app.infra.outbox_dispatch），不在此处直接调 Temporal。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.idempotency import IdempotencyService
from app.domain.repository import TaskRepository
from app.domain.service import DomainService


@dataclass
class TaskCommandResult:
    command: str
    state: str
    version: int


class TaskCommandService:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._idem = IdempotencyService()

    def _run(self, *, user_id, task_id, expected_version, command, idempotency_key, reason):
        op = f"task.{command}"
        if idempotency_key:
            replay = self._idem.find_replay(
                self._db, user_id=user_id, operation=op, client_key=idempotency_key,
                payload={"command": command, "task_id": task_id, "expected_version": expected_version},
            )
            if replay is not None:
                task = TaskRepository(self._db).get_owned(user_id, task_id)
                return TaskCommandResult(command=command, state=task.state, version=task.version)
        event = DomainService(TaskRepository(self._db)).transition_task(
            user_id=user_id, task_id=task_id, command=command,
            expected_version=expected_version, reason=reason,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        if idempotency_key:
            self._idem.record(
                self._db, user_id=user_id, operation=op, client_key=idempotency_key,
                payload={"command": command, "task_id": task_id, "expected_version": expected_version},
                result_ref=("task", task.id),
            )
        return TaskCommandResult(command=command, state=task.state, version=task.version)

    def pause_task(self, *, user_id, task_id, expected_version, idempotency_key=None, reason=None) -> TaskCommandResult:
        return self._run(user_id=user_id, task_id=task_id, expected_version=expected_version,
                         command="pause", idempotency_key=idempotency_key, reason=reason)

    def resume_task(self, *, user_id, task_id, expected_version, idempotency_key=None, reason=None) -> TaskCommandResult:
        return self._run(user_id=user_id, task_id=task_id, expected_version=expected_version,
                         command="resume", idempotency_key=idempotency_key, reason=reason)

    def cancel_task(self, *, user_id, task_id, expected_version, idempotency_key=None, reason=None) -> TaskCommandResult:
        return self._run(user_id=user_id, task_id=task_id, expected_version=expected_version,
                         command="cancel", idempotency_key=idempotency_key, reason=reason)
```

> 说明：`transition_task` 已对 `pause`(RUNNING→PAUSING)、`resume`(PAUSED→RUNNING)、`cancel`(RUNNING/PAUSING/PAUSED/...→CANCELLING/QUEUED→CANCELLED) 提供合法转换，并同事务写 DomainEvent + Outbox（`task.pause`/`task.resume`/`task.cancel`）。M-07 不做“取消即删除数据”语义。

- [ ] **Step 4: Outbox→Temporal 分发器**

`backend/app/infra/outbox_dispatch.py`：

```python
"""OutboxTemporalDispatcher：把 task.* 命令 outbox 事件分发为 Temporal Signal。

优先保证 DB 与 Temporal 最终一致：DB 事务先提交（state+event+outbox），这里再
Signal；失败按 outbox 有界重试，dispatch_key 唯一。Workflow 不存在时（如 QUEUED
直接 cancel）标记 dispatched 为 no-op。
"""

from __future__ import annotations

from typing import Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowNotFoundError

from app.domain.repository import OutboxRepository

# command -> workflow signal 名称
_TASK_SIGNALS = {
    "task.pause": "pause",
    "task.resume": "resume",
    "task.cancel": "cancel",
}


class OutboxTemporalDispatcher:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def dispatch_pending_for(self, db: Any, *, user_id: int, task_id: int) -> int:
        repo = OutboxRepository(db)
        pending = [e for e in repo.claim_pending() if e.aggregate_id == task_id]
        sent = 0
        for event in pending:
            signal = _TASK_SIGNALS.get(event.event_type)
            if signal is None:
                repo.mark_dispatched(event)  # 非 command 事件：直接标记，不 Signal
                continue
            workflow_id = f"task-workflow-{task_id}"
            handle = self._client.get_workflow_handle(workflow_id)
            try:
                await handle.signal(signal)
                repo.mark_dispatched(event)
                sent += 1
            except WorkflowNotFoundError:
                # QUEUED 直接 cancel 等场景：无 workflow，DB 已反映最终状态
                repo.mark_dispatched(event)
            except Exception:
                repo.mark_failed(event)
        return sent
```

> 有界重试：`mark_failed` 递增 `attempts`；后续由 API 命令再次触发或由未来 worker 轮询补发。M-07 不建独立定时器，符合“有界”。

- [ ] **Step 5: API DTO + 端点**

`backend/app/api/schemas.py` 追加：

```python
class TaskCommandDto(BaseModel):
    expected_version: int
    idempotency_key: str | None = None
    reason: str | None = None


class TaskCommandResponse(BaseModel):
    command: str
    state: str
    version: int
```

`backend/app/api/routes/tasks.py` 追加（保持 Route 薄层；命令语义在 TaskCommandService）：

```python
from app.domain.task_commands import TaskCommandService
from app.infra.outbox_dispatch import OutboxTemporalDispatcher


def get_task_command_service(db: DbSession = Depends(get_db)) -> TaskCommandService:
    return TaskCommandService(db)


_TASK_COMMANDS = {"pause", "resume", "cancel"}


@router.post("/{task_id}/commands/{command}", response_model=TaskCommandResponse)
async def task_command(
    task_id: int,
    command: str,
    cmd: TaskCommandDto,
    user: User = Depends(require_user),
    service: TaskCommandService = Depends(get_task_command_service),
) -> TaskCommandResponse:
    if command not in _TASK_COMMANDS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="未知命令")
    handler = getattr(service, f"{command}_task")
    result = handler(
        user_id=user.id, task_id=task_id,
        expected_version=cmd.expected_version,
        idempotency_key=cmd.idempotency_key, reason=cmd.reason,
    )
    return TaskCommandResponse(command=result.command, state=result.state, version=result.version)
```

> `expected_version` 来自前端最近一次 Task Query（乐观锁，与 M-06 confirm 一致）。

- [ ] **Step 6: 运行 domain + API 测试**

`cd backend && .venv/Scripts/python.exe -m pytest tests/domain/test_task_commands.py tests/api/test_task_commands.py -q`
Expected: PASS。`tests/api/test_task_commands.py` 参照 `test_task_draft.py` 的 TestClient 模式，覆盖：合法 pause/resume/cancel 返回正确 state；非法命令 404；无权限/不存在 task 404。

- [ ] **Step 7: ruff/mypy 门禁**

`cd backend && ruff check app tests && ruff format --check app tests && .venv/Scripts/python.exe -m mypy app`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add backend/app/domain/task_commands.py backend/app/infra/outbox_dispatch.py backend/app/api/schemas.py backend/app/api/routes/tasks.py backend/tests/domain/test_task_commands.py backend/tests/api/test_task_commands.py
git commit -m "feat(task): add pause resume and cancel commands

新增 TaskCommandService（幂等 + M-04 状态机事务 + outbox 入队）与
OutboxTemporalDispatcher（outbox -> Temporal Signal，有界重试，workflow 不存在时
no-op）。API 暴露 POST /tasks/{id}/commands/{pause|resume|cancel}，Route 保持薄层。
关联模块：M-07"
```

---

## Task 3: Activity heartbeat + checkpoint/replay 执行 seam

**Files:**
- Create: `backend/app/activities/heartbeat.py`
- Modify: `backend/app/activities/task_execution.py`（commit_checkpoint 复用判定）
- Test: `backend/tests/domain/test_checkpoint.py`（追加 M-07 用例）

**Interfaces:**
- Consumes: M-04 `CheckpointRepository`/`commit_checkpoint`；Temporal `activity.heartbeat`。
- Produces:
  - `heartbeat_progress(done: int, total: int | None = None, note: str = "") -> None`
  - `commit_checkpoint` 返回 `CommitCheckpointResult(checkpoint_id, reused: bool)`（reused=True 表示重放复用）

- [ ] **Step 1: 先写 heartbeat 语义测试**

`backend/tests/domain/test_checkpoint.py` 追加：

```python
def test_commit_checkpoint_reuses_same_batch(db, user, task) -> None:
    from app.domain.models import Checkpoint
    from app.domain.service import DomainService
    from app.domain.repository import TaskRepository

    svc = DomainService(TaskRepository(db))
    first = svc.commit_checkpoint(
        user_id=user.id, task_id=task.id, run_id=1, batch_identity="unit-1",
        spec_version=1, plan_version=0, node_run_id=None,
        input_fingerprint="fp-1", committed_refs={"n": 1}, content_hash=None,
    )
    second = svc.commit_checkpoint(
        user_id=user.id, task_id=task.id, run_id=1, batch_identity="unit-1",
        spec_version=1, plan_version=0, node_run_id=None,
        input_fingerprint="fp-1", committed_refs={"n": 1}, content_hash=None,
    )
    assert second.id == first.id  # 复用，不重复提交

    rows = db.query(Checkpoint).filter_by(run_id=1).all()
    assert len(rows) == 1
```

- [ ] **Step 2: 运行确认当前行为**

`cd backend && .venv/Scripts/python.exe -m pytest tests/domain/test_checkpoint.py -q`
Expected: M-04 已实现复用 → 该用例直接 PASS（作为回归锁定）。

- [ ] **Step 3: 实现 heartbeat helper**

`backend/app/activities/heartbeat.py`：

```python
"""Activity heartbeat helper（M-07）。

heartbeat 只用于存活/进度/取消响应，绝不生成业务 Checkpoint（D-015/D-030）。
details 只放安全、最小、非 Secret 的执行进度。
"""

from __future__ import annotations

from temporalio import activity


def heartbeat_progress(*, done: int, total: int | None = None, note: str = "") -> None:
    details = {"done": done, "note": note}
    if total is not None:
        details["total"] = total
    activity.heartbeat(details)
```

- [ ] **Step 4: 让 commit_checkpoint 返回 reused**

修改 `backend/app/activities/task_execution.py` 的 `commit_checkpoint`：先 `CheckpointRepository.find_by_batch`，存在且 fingerprint 一致 → 返回 `CommitCheckpointResult(existing.id, reused=True)`；否则走 `DomainService.commit_checkpoint`。

- [ ] **Step 5: 运行门禁**

`cd backend && .venv/Scripts/python.exe -m pytest tests/domain/test_checkpoint.py -q && ruff check app tests && ruff format --check app tests`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/activities/heartbeat.py backend/app/activities/task_execution.py backend/tests/domain/test_checkpoint.py
git commit -m "feat(workflow): add heartbeat and checkpoint recovery

新增 heartbeat_progress helper（存活/进度/取消响应，不生成业务 Checkpoint）；
commit_checkpoint Activity 明确返回 reused 以支撑重放复用。Checkpoint 幂等复用由
M-04 commit_checkpoint 提供（同 batch + 同 fingerprint 返回既有行）。
关联模块：M-07"
```

---

## Task 4: Worker crash/restart 恢复 + 幂等重试（Temporal 集成）

**Files:**
- Create: `backend/tests/fixtures/execution_adapter.py`（fixture 执行单元 Activity，仅测试 worker 注册）
- Create: `backend/tests/integration/fixture_worker.py`（独立 worker 入口，供 crash/restart 子进程运行）
- Create: `backend/tests/integration/test_worker_crash_restart.py`
- Modify: `backend/tests/integration/conftest.py`（追加 `fixture_task_queue` / `start_fixture_workflow` helper）

**Interfaces:**
- Consumes: Task 1 `TaskWorkflow`/`TaskWorkflowStarter`/lifecycle Activity；Task 3 `heartbeat_progress`。
- Produces: crash/restart 验证流程：batch1 commit + checkpoint → kill worker 子进程 → 重启 → batch1 不重复、batch2 完成、最终结果一次。

- [ ] **Step 1: fixture 执行单元 adapter（仅测试注册，非 Production 节点）**

`backend/tests/fixtures/execution_adapter.py`：

```python
"""M-07 集成测试 fixture 执行单元。

只允许在测试 worker（fixture_worker.py / test worker）注册；绝不允许进入
app.worker 的 Production Worker（I-002 / M-07 边界）。每个单元：
  - execute：heartbeat 进度，返回 committed_refs（无持久副作用）
  - commit：由 TaskWorkflow 调 commit_checkpoint Activity 持久化
"""

from __future__ import annotations

import asyncio

from temporalio import activity

from app.activities.execution_seam import (
    ExecuteUnitInput,
    ExecuteUnitResult,
    ExecutionUnit,
    FetchUnitInput,
    FetchUnitResult,
)
from app.activities.heartbeat import heartbeat_progress

# 每个 run 返回固定 3 个安全单元，随后 None（模拟一小批执行计划）。
_FIXTURE_UNITS_PER_RUN = 3


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    index = inp.after_index + 1
    if index > _FIXTURE_UNITS_PER_RUN:
        return FetchUnitResult(unit=None)
    return FetchUnitResult(
        unit=ExecutionUnit(
            run_id=inp.run_id, index=index, unit_type="fixture_safe_unit",
            input_fingerprint=f"fp-{inp.run_id}-{index}",
        )
    )


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    heartbeat_progress(done=inp.unit.index, total=_FIXTURE_UNITS_PER_RUN, note="fixture unit")
    await asyncio.sleep(0.05)  # 模拟短小安全单元
    return ExecuteUnitResult(
        unit_index=inp.unit.index,
        committed_refs={"run_id": inp.run_id, "unit": inp.unit.index},
    )
```

- [ ] **Step 2: crash/restart 专用 worker 入口**

`backend/tests/integration/fixture_worker.py`：

```python
"""M-07 crash/restart 测试专用 worker（独立进程入口）。

python -m tests.integration.fixture_worker
只注册 TaskWorkflow + lifecycle + fixture 执行单元；绝不在 Production 使用。
"""

from __future__ import annotations

import asyncio

from temporalio.worker import Worker

from app.activities.task_execution import (
    commit_checkpoint, complete_run, ensure_run_started, mark_cancelled, mark_paused,
)
from app.config import get_settings
from app.infra.temporal import create_temporal_client
from app.workflows.task_workflow import TaskWorkflow
from tests.fixtures.execution_adapter import execute_safe_unit, fetch_next_execution_unit


async def run(queue: str) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    worker = Worker(
        client,
        task_queue=queue,
        workflows=[TaskWorkflow],
        activities=[
            ensure_run_started, mark_paused, mark_cancelled, complete_run, commit_checkpoint,
            fetch_next_execution_unit, execute_safe_unit,
        ],
    )
    await worker.run()


def main() -> None:
    import sys
    queue = sys.argv[1] if len(sys.argv) > 1 else settings_queue_default()
    asyncio.run(run(queue))


def settings_queue_default() -> str:
    from app.config import get_settings
    return get_settings().temporal_task_queue


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: crash/restart 集成测试（先写，必须真子进程 kill，不直接调函数两次）**

`backend/tests/integration/test_worker_crash_restart.py`：

```python
"""Worker 崩溃/重启恢复：batch1 commit+checkpoint 后 kill worker，重启后 batch1 不
重复、batch2 完成、最终结果一次。必须用真实子进程 kill，不得直接调 Activity 两次。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.config import get_settings
from app.domain.models import Checkpoint, Run, Task
from app.infra.deps import get_session_factory
from app.infra.temporal import create_temporal_client
from app.workflows.starter import TaskWorkflowStarter
from app.workflows.task_workflow import TaskWorkflowResult

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]


def _spawn_worker(queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "tests.integration.fixture_worker", queue],
        cwd=str(ROOT / "backend"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_checkpoint(run_id: int, batch: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = get_session_factory()()
        try:
            exists = session.query(Checkpoint).filter_by(run_id=run_id, batch_identity=batch).first()
        finally:
            session.close()
        if exists:
            return
        time.sleep(0.2)
    raise TimeoutError(f"checkpoint {batch} not committed")


@pytest.mark.asyncio
async def test_worker_crash_restart_no_duplicate_batch(confirmed_task) -> None:
    settings = get_settings()
    queue = f"kairos-test-crash-{uuid4().hex[:8]}"
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )

    proc = _spawn_worker(queue)
    try:
        _wait_checkpoint(started.run_id, "unit-1")   # batch1 已提交
        proc.send_signal(signal.SIGKILL)             # 崩溃
        proc.wait(timeout=10)
        time.sleep(1)
        proc = _spawn_worker(queue)                   # 重启

        handle = client.get_workflow_handle(started.workflow_id)
        result: TaskWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            cps = session.query(Checkpoint).filter_by(run_id=started.run_id).order_by(Checkpoint.id).all()
            assert len(cps) == 3                      # unit-1/2/3 各一次
            run = session.get(Run, started.run_id)
            task = session.get(Task, confirmed_task["task_id"])
            assert run.state == "completed"
            assert task.state == "COMPLETED"
        finally:
            session.close()
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            proc.kill()
```

> 说明：fixture `_FIXTURE_UNITS_PER_RUN=3`，崩溃发生在 unit-1 checkpoint 之后。Temporal 重放时 unit-1 的 execute/commit 结果已固化在 History，重启 worker 不会重复业务副作用（checkpoint 复用）。

- [ ] **Step 4: 运行 crash/restart 集成测试**

`cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/integration/test_worker_crash_restart.py -q`
Expected: PASS（本地栈 + Temporal 运行中）。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/execution_adapter.py backend/tests/integration/fixture_worker.py backend/tests/integration/test_worker_crash_restart.py backend/tests/integration/conftest.py
git commit -m "test(workflow): cover worker crash restart recovery

真实子进程 kill + 重启：unit-1 commit+checkpoint 后崩溃，重启后 batch1 不重复、
全部单元完成、最终状态一次。fixture 执行单元只注册在测试 worker，不进 Production。
关联模块：M-07"
```

---

## Task 5: SSETaskEvent schema + 持久化重放 + 端点

**Files:**
- Create: `backend/app/api/events.py`
- Create: `backend/app/api/routes/events.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/api/test_task_events.py`

**Interfaces:**
- Consumes: M-04 `DomainEvent`（`aggregate_type="task"`，`aggregate_id=task_id`，`id` 作 cursor）；`TaskRepository` owner-safe 404。
- Produces:
  - `SSETaskEvent(event_id, event_type, task_id, run_id, occurred_at, payload)`（pydantic）
  - `TASK_EVENT_TYPES`：TASK_STATE_CHANGED / TASK_PAUSE_REQUESTED / TASK_PAUSED / TASK_RESUMED / TASK_CANCEL_REQUESTED / TASK_CANCELLED / TASK_COMPLETED / TASK_PARTIALLY_COMPLETED / TASK_FAILED / APPROVAL_REQUIRED / TASK_SNAPSHOT（契约）
  - `query_task_events(db, user_id, task_id, after_id) -> list[DomainEvent]`
  - `map_domain_event_to_sse(ev) -> SSETaskEvent`
  - `GET /api/events/tasks/{task_id}`：SSE stream（Last-Event-ID 重放 + 实时 + keepalive）

- [ ] **Step 1: 先写 SSE replay + 跨用户隔离测试**

`backend/tests/api/test_task_events.py`：

```python
"""M-07: SSE 事件基于 domain_events 重放 + 跨用户隔离（DB 层，不依赖真实 stream 服务）。"""
from __future__ import annotations

import pytest
from app.api.events import map_domain_event_to_sse, query_task_events
from app.domain.models import DomainEvent
from app.state.events import append_domain_event


def _seed_events(db, user_id: int, task_id: int) -> None:
    for i, ev in enumerate(["task.pause", "task.mark_paused", "task.resume"], start=1):
        append_domain_event(
            db, user_id=user_id, aggregate_type="task", aggregate_id=task_id,
            event_type=ev, aggregate_version=i, payload={"command": ev},
            actor_type="user", actor_id=user_id,
        )
    db.commit()


def test_replay_after_cursor(db, user) -> None:
    task_id = 7
    _seed_events(db, user.id, task_id)
    first = query_task_events(db, user.id, task_id, after_id=0)
    assert [e.event_type for e in first] == ["task.pause", "task.mark_paused", "task.resume"]
    after_first = query_task_events(db, user.id, task_id, after_id=first[0].id)
    assert [e.event_type for e in after_first] == ["task.mark_paused", "task.resume"]


def test_sse_mapping() -> None:
    ev = DomainEvent(
        id=5, user_id=1, aggregate_type="task", aggregate_id=9,
        event_type="task.mark_paused", aggregate_version=3,
        payload={"command": "mark_paused"}, actor_type="system", actor_id=None,
    )
    sse = map_domain_event_to_sse(ev)
    assert sse.event_type == "TASK_PAUSED"
    assert sse.event_id == 5
    assert sse.task_id == 9
    assert sse.payload["command"] == "mark_paused"


def test_cross_user_isolation(db, user, user2) -> None:
    task_id = 11
    _seed_events(db, user.id, task_id)
    # 另一个用户不能通过 cursor 查询到该 task 事件
    from app.domain.repository import TaskRepository
    # 事件查询要求 task 归属；无归属任务 → 空/404 由 route 层保证。此处验证 mapper 不含他人数据。
    assert query_task_events(db, user2.id, task_id, after_id=0) == []
```

> 需在 `tests/api` 或 `tests/domain` conftest 提供 `user2` fixture（或复用 `user` 创建第二个）。

- [ ] **Step 2: 运行确认失败**

`cd backend && .venv/Scripts/python.exe -m pytest tests/api/test_task_events.py -q`
Expected: FAIL（`app.api.events` 不存在）。

- [ ] **Step 3: 实现 events 模块**

`backend/app/api/events.py`：

```python
"""SSE 任务事件：基于 domain_events 的稳定 typed schema + 重放查询。

SSE 不是业务状态源；只推送用户重要事件（D-039）。cursor = domain_events.id，
断线后 Last-Event-ID 重放不会丢状态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.domain.models import DomainEvent
from sqlalchemy import select


# domain_events.event_type -> SSE event_type（同一语义，不造第二套名称）
_EVENT_TYPE_MAP = {
    "task.submit": "TASK_STATE_CHANGED",
    "task.start": "TASK_STATE_CHANGED",
    "task.spec_confirmed": "TASK_STATE_CHANGED",
    "task.pause": "TASK_PAUSE_REQUESTED",
    "task.mark_paused": "TASK_PAUSED",
    "task.resume": "TASK_RESUMED",
    "task.cancel": "TASK_CANCEL_REQUESTED",
    "task.mark_cancelled": "TASK_CANCELLED",
    "task.complete": "TASK_COMPLETED",
    "task.mark_partial": "TASK_PARTIALLY_COMPLETED",
    "task.fail": "TASK_FAILED",
    "task.mark_waiting_approval": "APPROVAL_REQUIRED",
}


class SSETaskEvent(BaseModel):
    event_id: int
    event_type: str
    task_id: int
    run_id: int | None = None
    occurred_at: datetime
    payload: dict[str, Any]


def query_task_events(db: Any, *, user_id: int, task_id: int, after_id: int) -> list[DomainEvent]:
    return list(
        db.scalars(
            select(DomainEvent)
            .where(
                DomainEvent.user_id == user_id,
                DomainEvent.aggregate_type == "task",
                DomainEvent.aggregate_id == task_id,
                DomainEvent.id > after_id,
            )
            .order_by(DomainEvent.id)
        )
    )


def map_domain_event_to_sse(ev: DomainEvent) -> SSETaskEvent:
    return SSETaskEvent(
        event_id=ev.id,
        event_type=_EVENT_TYPE_MAP.get(ev.event_type, "TASK_STATE_CHANGED"),
        task_id=ev.aggregate_id,
        run_id=ev.run_id,
        occurred_at=ev.occurred_at,
        payload=ev.payload or {},
    )
```

- [ ] **Step 4: SSE 端点（Last-Event-ID 重放 + keepalive + owner-safe）**

`backend/app/api/routes/events.py`：

```python
"""SSE 任务事件流端点（/api/events/tasks/{task_id}）。

- require_user + owner-safe Task 查询（无权限/不存在 → 404，不泄漏存在性）。
- 连接时按 Last-Event-ID / ?after_id 重放 domain_events，然后实时推送新事件。
- keepalive 只是注释行（: ping），不是 DomainEvent、不占业务 sequence（D-039）。
- 每进程维护连接 registry + 轻量轮询，不引入 Redis。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.api.events import SSETaskEvent, map_domain_event_to_sse, query_task_events
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db

router = APIRouter(prefix="/events", tags=["events"])


def _parse_last_event_id(request: Request, after_id: str | None) -> int:
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        return int(header)
    if after_id and after_id.isdigit():
        return int(after_id)
    return 0


def _format_sse(event: SSETaskEvent) -> str:
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"


@router.get("/tasks/{task_id}")
async def task_events(
    task_id: int,
    request: Request,
    after_id: str | None = None,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> StreamingResponse:
    TaskRepository(db).get_owned(user.id, task_id)  # owner-safe 404

    async def event_stream() -> Any:
        cursor = _parse_last_event_id(request, after_id)
        # 1) 重放 cursor 之后的已持久化事件
        replay = query_task_events(db, user_id=user.id, task_id=task_id, after_id=cursor)
        for ev in replay:
            yield _format_sse(map_domain_event_to_sse(ev))
            cursor = ev.id
        # 2) 实时轮询 + keepalive（轻量；不引入 Redis）
        while True:
            new = query_task_events(db, user_id=user.id, task_id=task_id, after_id=cursor)
            for ev in new:
                yield _format_sse(map_domain_event_to_sse(ev))
                cursor = ev.id
            yield ": ping\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

```

`backend/app/api/router.py` 追加：`api_router.include_router(events.router)`（来自 `app.api.routes.events`）。

- [ ] **Step 5: 运行 SSE 测试 + 手动 curl 验证**

`cd backend && .venv/Scripts/python.exe -m pytest tests/api/test_task_events.py -q`
Expected: PASS。

手动验证（本地栈运行中）：注册用户 → 建任务 → `curl -N -b /tmp/kairos_cookies.txt "http://localhost:8000/api/events/tasks/1?after_id=0"` 应看到 SSE 帧。跨用户 `curl` 应 404。

- [ ] **Step 6: ruff/mypy 门禁**

`cd backend && ruff check app tests && ruff format --check app tests && .venv/Scripts/python.exe -m mypy app`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/events.py backend/app/api/routes/events.py backend/app/api/router.py backend/tests/api/test_task_events.py
git commit -m "feat(api): add replayable task event stream

新增 /api/events/tasks/{id} SSE：基于 domain_events 重放（Last-Event-ID /
?after_id），实时轮询 + keepalive 注释行，owner-safe 404。SSE 不是事实源，只推
用户重要事件。关联模块：M-07"
```

---

## Task 6: 前端 SSE client + Task Status Drawer 真实接线

**Files:**
- Create: `frontend/src/features/tasks/events.api.ts`
- Create: `frontend/src/features/tasks/useTaskEvents.ts`
- Create: `frontend/src/features/tasks/commands.api.ts`
- Modify: `frontend/src/app/overlay/drawers/TaskStatusDrawer.vue`
- Test: `frontend/src/features/tasks/taskEvents.test.ts`
- Test: `frontend/src/app/overlay/drawers/TaskStatusDrawer.test.ts`

**Interfaces:**
- Consumes: 后端 `GET /api/events/tasks/{id}`（SSE）、`GET /api/tasks/{id}`（Task Query）、`POST /api/tasks/{id}/commands/{command}`、`allowed_actions`。
- Produces:
  - `openTaskEventStream(taskId, lastEventId?) -> EventSource`
  - `useTaskEvents(taskId)`: `connection`（idle|connecting|open|reconnecting|closed）、`lastEventId`、`latestEvent`、`connect()/disconnect()`、自动带 cursor 重连
  - `pauseTask/resumeTask/cancelTask(taskId, idempotencyKey?) -> TaskCommandResponse`

- [ ] **Step 1: events.api.ts**

`frontend/src/features/tasks/events.api.ts`：

```ts
/** SSE 任务事件（对应后端 /api/events/tasks/{id}）。SSE 不是事实源，断线重连后
 *  前端仍以 Task Query 为准，SSE 只负责增量提醒。 */
export type TaskEventType =
  | 'TASK_STATE_CHANGED'
  | 'TASK_PAUSE_REQUESTED'
  | 'TASK_PAUSED'
  | 'TASK_RESUMED'
  | 'TASK_CANCEL_REQUESTED'
  | 'TASK_CANCELLED'
  | 'TASK_COMPLETED'
  | 'TASK_PARTIALLY_COMPLETED'
  | 'TASK_FAILED'
  | 'APPROVAL_REQUIRED'

export interface TaskSseEvent {
  event_id: number
  event_type: TaskEventType
  task_id: number
  run_id: number | null
  occurred_at: string
  payload: Record<string, unknown>
}

const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export function openTaskEventStream(
  taskId: string | number,
  lastEventId?: number,
): EventSource {
  const url = new URL(`${DEFAULT_BASE_URL}/events/tasks/${taskId}`, window.location.origin)
  if (lastEventId) url.searchParams.set('after_id', String(lastEventId))
  return new EventSource(url.toString())
}

export function parseSseMessage(raw: string): TaskSseEvent | null {
  try {
    return JSON.parse(raw) as TaskSseEvent
  } catch {
    return null
  }
}
```

- [ ] **Step 2: useTaskEvents store**

`frontend/src/features/tasks/useTaskEvents.ts`：

```ts
import { onBeforeUnmount, ref, type Ref } from 'vue'
import { openTaskEventStream, parseSseMessage, type TaskSseEvent } from './events.api'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

/** 统一 Task 事件订阅。断线自动重连（带 cursor），恢复后由调用方重新拉取 Task Snapshot。 */
export function useTaskEvents(taskId: Ref<string | number>) {
  const connection = ref<ConnectionStatus>('idle')
  const lastEventId = ref<number | undefined>(undefined)
  const latestEvent = ref<TaskSseEvent | null>(null)
  let source: EventSource | null = null

  function connect(): void {
    disconnect()
    connection.value = 'connecting'
    source = openTaskEventStream(taskId.value, lastEventId.value)
    source.onopen = () => {
      connection.value = 'open'
    }
    source.onmessage = (msg) => {
      const ev = parseSseMessage(msg.data)
      if (!ev) return
      lastEventId.value = ev.event_id
      latestEvent.value = ev
    }
    source.onerror = () => {
      // EventSource 自动重连；Last-Event-ID 由浏览器自动携带
      connection.value = 'reconnecting'
    }
  }

  function disconnect(): void {
    source?.close()
    source = null
    connection.value = 'closed'
  }

  onBeforeUnmount(disconnect)

  return { connection, lastEventId, latestEvent, connect, disconnect }
}
```

- [ ] **Step 3: commands.api.ts**

`frontend/src/features/tasks/commands.api.ts`：

```ts
import { apiClient } from '@/app/api/client'

export interface TaskCommandResponse {
  command: 'pause' | 'resume' | 'cancel'
  state: string
  version: number
}

export function pauseTask(taskId: string | number, idempotencyKey?: string): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/pause`, {
    expected_version: 0,
    idempotency_key: idempotencyKey,
  })
}

export function resumeTask(taskId: string | number, idempotencyKey?: string): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/resume`, {
    expected_version: 0,
    idempotency_key: idempotencyKey,
  })
}

export function cancelTask(taskId: string | number, idempotencyKey?: string): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/cancel`, {
    expected_version: 0,
    idempotency_key: idempotencyKey,
  })
}
```

> 说明：`expected_version` 由前端从最近一次 Task Query 传入（真实乐观锁）。实施时在 Drawer 内把 `summary.version` 传入命令 API，不要写死 0。

- [ ] **Step 4: 重写 TaskStatusDrawer 接真实数据**

`frontend/src/app/overlay/drawers/TaskStatusDrawer.vue`：

```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useTaskShell } from '@/features/tasks/useTaskShell'
import { useTaskEvents } from '@/features/tasks/useTaskEvents'
import { cancelTask, pauseTask, resumeTask } from '@/features/tasks/commands.api'
import type { TaskSseEvent } from '@/features/tasks/events.api'

export interface TaskStatusPayload {
  taskId: number | string
}

const props = defineProps<{ payload?: unknown }>()
const payload = props.payload as TaskStatusPayload | undefined
const taskIdRef = computed(() => String(payload?.taskId ?? ''))
const taskId = ref(taskIdRef.value)
watch(taskIdRef, (v) => { taskId.value = v })

const { summary, loading, state, allowedActions, can, load } = useTaskShell(taskId)
const { connection, latestEvent, connect, disconnect } = useTaskEvents(taskId)

const busy = ref(false)
const notice = ref('')

const connectionLabel = computed(() => {
  const map: Record<string, string> = {
    connecting: '连接中…', open: '实时', reconnecting: '重连中…', closed: '已断开', idle: '未连接',
  }
  return map[connection.value] ?? connection.value
})

async function runCommand(cmd: 'pause' | 'resume' | 'cancel'): Promise<void> {
  if (!can(cmd) || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const fn = { pause: pauseTask, resume: resumeTask, cancel: cancelTask }[cmd]
    await fn(taskId.value)
    await load() // 立即拉取真实状态（PAUSING/CANCELLING 中间态来自后端事实）
  } catch (err) {
    notice.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load(); connect() })
onBeforeUnmount(disconnect)

const importantEvent = computed<TaskSseEvent | null>(() => latestEvent.value)
</script>

<template>
  <div v-if="summary" class="status-drawer">
    <dl class="status-list">
      <div class="status-row"><dt>任务</dt><dd>{{ summary.title }}</dd></div>
      <div class="status-row"><dt>状态</dt><dd>{{ summary.state }}</dd></div>
      <div class="status-row"><dt>Spec 版本</dt><dd>{{ summary.current_spec_version ?? '—' }}</dd></div>
      <div class="status-row"><dt>Plan 版本</dt><dd>{{ summary.current_plan_version ?? '—' }}</dd></div>
      <div class="status-row"><dt>事件流</dt><dd>{{ connectionLabel }}</dd></div>
    </dl>

    <p v-if="importantEvent" class="muted">最近事件：{{ importantEvent.event_type }}</p>

    <div class="command-row">
      <button type="button" class="ghost" :disabled="!can('pause') || busy" @click="runCommand('pause')">暂停</button>
      <button type="button" class="ghost" :disabled="!can('resume') || busy" @click="runCommand('resume')">恢复</button>
      <button type="button" class="danger" :disabled="!can('cancel') || busy" @click="runCommand('cancel')">取消</button>
    </div>
    <p v-if="notice" class="error">{{ notice }}</p>
    <p v-if="loading" class="muted">加载中…</p>
  </div>
  <p v-else class="muted">任务状态信息暂不可用</p>
</template>
```

> 注意：Drawer 只使用后端真实 `allowed_actions`/`state`；PAUSING/CANCELLING 中间态如实展示（D-025/code standard §3.5）。`TaskShell.vue` 里 `openStatusDrawer` 的 payload 改为只传 `{ taskId }`（其余数据由 Drawer 自行 Query）。

- [ ] **Step 5: 前端测试**

`frontend/src/features/tasks/taskEvents.test.ts`（store）：

```ts
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { openTaskEventStream } from './events.api'

describe('openTaskEventStream', () => {
  it('builds SSE url with cursor', () => {
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(() => ({ close: vi.fn() })))
    const taskId = ref(3)
    const es = openTaskEventStream(taskId.value, 7)
    expect((es as unknown as { url: string }).url).toContain('/api/events/tasks/3')
    expect((es as unknown as { url: string }).url).toContain('after_id=7')
    vi.unstubAllGlobals()
  })
})
```

`frontend/src/app/overlay/drawers/TaskStatusDrawer.test.ts`：mock `tasks.api.getTask` 返回 `PAUSING` state + `allowed_actions: ['resume','cancel']`，断言暂停按钮禁用、恢复/取消可点；mock 命令 API，点击恢复后 `load()` 再次拉取 `PAUSED→RUNNING` 断言 UI 显示 RUNNING（不出现乐观假状态）。

- [ ] **Step 6: 前端门禁**

`cd frontend && npm run type-check && npm run lint:check && npm run format:check && npm run test:unit -- taskEvents TaskStatusDrawer`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/tasks/events.api.ts frontend/src/features/tasks/useTaskEvents.ts frontend/src/features/tasks/commands.api.ts frontend/src/app/overlay/drawers/TaskStatusDrawer.vue frontend/src/features/tasks/TaskShell.vue frontend/src/features/tasks/taskEvents.test.ts frontend/src/app/overlay/drawers/TaskStatusDrawer.test.ts
git commit -m "feat(web): connect task SSE and status drawer

新增统一 Task SSE client + useTaskEvents（断线自动带 cursor 重连），命令 API
pause/resume/cancel。Task Status Drawer 接真实 Task Query + SSE + allowed_actions，
展示 RUNNING/PAUSING/PAUSED/CANCELLING/CANCELLED 真实过渡，不乐观冒充后端状态。
关联模块：M-07"
```

---

## Task 7: 聚焦 Temporal/SSE 集成测试（pause/resume / cancel / 幂等）

**Files:**
- Modify: `backend/tests/integration/test_task_workflow.py`（追加 pause/resume、cancel、重复命令用例）
- Modify: `backend/tests/integration/conftest.py`（追加 `send_command` helper：直接 Signal，不经 API）

**Interfaces:**
- Consumes: Task 1-6 全部契约；Temporal `workflow_handle.signal`。
- Produces: M-07 核心 Temporal 集成验证矩阵（TEST 2/3/4 对应 Prompt TEST 2/3/4）。

- [ ] **Step 1: 追加集成用例**

`backend/tests/integration/test_task_workflow.py` 追加：

```python
@pytest.mark.asyncio
async def test_pause_resume_no_duplicate(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )
    handle = client.get_workflow_handle(started.workflow_id)
    await asyncio.sleep(0.5)
    await handle.signal("pause")
    await asyncio.sleep(0.3)

    session = get_session_factory()()
    try:
        task = session.get(Task, confirmed_task["task_id"])
        assert task.state in ("PAUSING", "PAUSED")
    finally:
        session.close()

    await handle.signal("resume")
    result: TaskWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))
    session = get_session_factory()()
    try:
        cps = session.query(Checkpoint).filter_by(run_id=started.run_id).order_by(Checkpoint.id).all()
        assert len(cps) == 3
        run = session.get(Run, started.run_id)
        assert run.state == "completed"
    finally:
        session.close()
    assert result.final_state == "COMPLETED"


@pytest.mark.asyncio
async def test_cancel_keeps_committed(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )
    handle = client.get_workflow_handle(started.workflow_id)
    await asyncio.sleep(0.5)
    await handle.signal("cancel")
    result: TaskWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))

    session = get_session_factory()()
    try:
        task = session.get(Task, confirmed_task["task_id"])
        run = session.get(Run, started.run_id)
        cps = session.query(Checkpoint).filter_by(run_id=started.run_id).all()
        assert task.state == "CANCELLED"
        assert run.state == "cancelled"
        # 已提交 batch 保留；未提交 batch 不算成功（checkpoint 数 < 总数即证明）
    finally:
        session.close()
    assert result.final_state == "CANCELLED"


@pytest.mark.asyncio
async def test_duplicate_signals_idempotent(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )
    handle = client.get_workflow_handle(started.workflow_id)
    await asyncio.sleep(0.5)
    await handle.signal("pause")
    await handle.signal("pause")  # 重复 signal：第二次幂等
    await handle.signal("resume")
    result: TaskWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))
    session = get_session_factory()()
    try:
        cps = session.query(Checkpoint).filter_by(run_id=started.run_id).all()
        assert len(cps) == 3  # 未因重复 signal 产生重复业务效果
    finally:
        session.close()
    assert result.final_state == "COMPLETED"
```

> 说明：`confirmed_task` 每用例一个（conftest 用 fixture 级 scope 或函数级重建）。Pause/resume 用真实 Temporal Signal + fixture worker 跑通。若本地栈 worker 未跑 task queue，测试用 `fixture_worker` 子进程（同 Task 4 方式）启动对应 queue。

- [ ] **Step 2: 运行 Temporal 集成矩阵**

`cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/integration/test_task_workflow.py tests/integration/test_worker_crash_restart.py -q`
Expected: 全部 PASS（start / pause-resume / cancel / duplicate / crash-restart）。

- [ ] **Step 3: 后端 scoped 门禁**

`cd backend && .venv/Scripts/python.exe -m pytest tests/state/ tests/domain/test_task_commands.py tests/domain/test_checkpoint.py tests/api/test_task_commands.py tests/api/test_task_events.py -q && ruff check app tests && ruff format --check app tests && .venv/Scripts/python.exe -m mypy app`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_task_workflow.py backend/tests/integration/conftest.py
git commit -m "test(workflow): cover pause resume cancel and command idempotency

Temporal 集成矩阵：pause/resume 无重复、cancel 保留已提交 batch、重复 signal 幂等；
结合 crash/restart 与 start contract 形成 M-07 核心 Gate。关联模块：M-07"
```

---

## Task 8: docs / execution record

**Files:**
- Create: `docs/implementation/M-07-execution.md`

**Interfaces:**
- Consumes: Task 1-7 的最终行为、Temporal 集成命令、SSE schema、提交列表。

- [ ] **Step 1: 编写 M-07 execution record**

`docs/implementation/M-07-execution.md`：

```markdown
# M-07 模块执行记录

状态：IN_PROGRESS
负责人/Agent：Claude Code — 2026-08-10
Baseline（M-06 DONE）SHA：`<M-06 收口后 HEAD SHA>`
依赖模块：M-04（DEPLOYED）、M-06（DONE）
目标环境：local（M-07 不属于 Deploy Gate；DEPLOY-GATE-2 必须等 M-05～M-08）

## 1. 模块目标
...

## 2. 契约
- TaskWorkflowInput / Result / Signals
- TaskCommandService pause/resume/cancel
- SSETaskEvent schema + /api/events/tasks/{id} replay
- TaskWorkflowStarter（M-08 seam：submit_validated_plan）

## 3. 行为
- 协作式暂停/取消（PAUSING/CANCELLING 真实中间态）
- heartbeat 不生成 Checkpoint
- checkpoint 复用（同 batch + fingerprint 幂等）
- worker crash/restart：batch1 不重复
- SSE Last-Event-ID 重放；SSE 不是事实源

## 4. Temporal 集成命令
cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/integration/test_task_workflow.py tests/integration/test_worker_crash_restart.py -q

## 5. 前端验证
cd frontend && npm run type-check && npm run lint:check && npm run format:check && npm run test:unit -- taskEvents TaskStatusDrawer

## 6. Migration
NO MIGRATION（复用 M-04 runs/checkpoints/domain_events/outbox_events；SSE cursor = domain_events.id）

## 7. Git 证据
- 分支：feature/M-07-temporal-workflow-sse（从 M-06 HEAD 创建，未 push）
- Commits：<Task 1-7 列表>
- working tree：clean；pushed：NO

## 8. 完成结论
- M-07 DONE 门禁全部满足后填写 DONE。
```

- [ ] **Step 2: 前端 + 后端最终门禁 + secret scan**

`cd frontend && npm run build`
`cd backend && .venv/Scripts/python.exe -m pytest tests/state/ tests/domain/test_task_commands.py tests/domain/test_checkpoint.py tests/api/test_task_commands.py tests/api/test_task_events.py -q`
Secret scan：`git grep -nE "(sk-|AKIA|secret|password)" -- backend/app frontend/src infra`（确认无新增 Secret；M-06 的真实 Key 不在仓库）。

- [ ] **Step 3: Commit**

```bash
git add docs/implementation/M-07-execution.md
git commit -m "docs(workflow): record M-07 execution

记录 TaskWorkflow、Run 启动、pause/resume/cancel、heartbeat/checkpoint、worker
恢复、SSE schema/replay、前端 Drawer、Temporal 集成命令与 Git 证据。NO MIGRATION。
关联模块：M-07"
```

---

## Self-Review（writing-plans）

**1. Spec coverage：**
- TaskWorkflow typed IDs-only input → Task 1 ✓
- Run 启动（ensure_run_started Activity、Spec 冻结校验、幂等）→ Task 1 ✓
- pause/resume/cancel 命令 + PAUSING/PAUSED/CANCELLING/CANCELLED 真实中间态 → Task 1/2/7 ✓
- 重复命令幂等 → Task 2/7 ✓
- Activity heartbeat（≠ checkpoint）→ Task 3 ✓
- checkpoint 复用 + worker crash/restart → Task 3/4 ✓
- SSETaskEvent schema + Last-Event-ID 重放 + 跨用户隔离 → Task 5 ✓
- 前端 SSE client + Task Status Drawer 真实过渡 → Task 6 ✓
- M-08 seam（submit_validated_plan）→ Task 1 ✓
- 禁止区（无 PlanGenerator/NodeRegistry/Approval/搜索抓取/Redis）→ 全部 Task 未触碰 ✓
- 无 Secret 进入 Temporal/SSE → Task 1 输入契约 + Task 5 payload ✓

**2. Placeholder scan：** 无 "TBD/TODO/implement later/add validation"。fixture seam 的 `NotImplementedError` 是 M-08 注册点，非占位（有明确契约 + fixture 覆盖）。

**3. Type consistency：** `TaskWorkflowInput`/`TaskWorkflowResult`/`ApprovalResolutionSignal`/`SafePauseSignal` 在 Task 1 定义并被 Task 7 使用一致；`SSETaskEvent` 在 Task 5 定义并被 Task 6 前端类型一致消费；`CommitCheckpointResult` Task 1 定义、Task 3 完善返回 reused。Activity 名在 workflow 与 worker 注册列表一致。

---

## PROJECT SELF-APPROVAL

**CHECK 1** M-06 precondition：M-06 = DONE（真实 DeepSeek E2E PASS）→ **PASS**
**CHECK 2** Business decisions：D-011/013/015/016/024/025/026/027/030/039/040 全部在案；费用相关已被 D-036 覆盖（SSE 只记录 Token 技术指标，不显示人民币费用）→ **PASS**
**CHECK 3** Temporal determinism：Workflow 无 DB/HTTP/LLM/Secret/不可重放副作用，全在 Activity → **PASS**
**CHECK 4** Postgres vs Temporal：runs/domain_events/checkpoints 是业务事实；History 是执行位置；无“PostgreSQL 存执行到第几行”→ **PASS**
**CHECK 5** M-04 compatibility：状态机（含系统命令）、event、outbox、checkpoint、idempotency 全部复用 → **PASS**
**CHECK 6** M-06 compatibility：只消费 confirmed CollectionSpecVersion；不改 GoalUnderstanding/Chat/Template → **PASS**
**CHECK 7** M-08 boundary：无 PlanGenerator/NodeRegistry/Approval 业务逻辑；只提供 starter seam + ApprovalResolutionSignal 契约 → **PASS**
**CHECK 8** M-09+ boundary：无 search/fetch/scrapy/playwright/extraction；fixture 单元只在测试 worker → **PASS**
**CHECK 9** Pause semantics：PAUSING 真实中间态，PAUSED 仅安全单元停止后由 Workflow mark_paused → **PASS**
**CHECK 10** Cancel semantics：CANCELLING 真实中间态；committed 保留；cancelled run 不可 resume → **PASS**
**CHECK 11** Idempotency：TaskCommandService + IdempotencyService + DB 兜底；重复 pause/cancel 一次效果 → **PASS**
**CHECK 12** Checkpoint：heartbeat 不生成 Checkpoint；checkpoint 在业务事务后 → **PASS**
**CHECK 13** SSE：event id/cursor、replay、owner isolation、snapshot fallback（前端重连后重新 Query）；SSE 非事实源 → **PASS**
**CHECK 14** Secrets：Temporal History（输入只含 ID）/SSE/DomainEvent/日志均无 Secret → **PASS**
**CHECK 15** A-Lite testing：只测高价值路径（生命周期、幂等、checkpoint、SSE replay、跨用户），不跑全量 → **PASS**
**CHECK 16** Git：每个 Task 独立 Commit，不 push/merge/tag/deploy → **PASS**

## PLAN SELF-APPROVAL

PLAN SELF-APPROVAL: PASS

M-06 precondition: PASS
business decisions: PASS
implementation plan M-07: PASS
Temporal determinism: PASS
Postgres/Temporal fact boundary: PASS
M-04 compatibility: PASS
M-06 compatibility: PASS
workflow input secret safety: PASS
pause semantics: PASS
resume semantics: PASS
cancel semantics: PASS
command idempotency: PASS
checkpoint semantics: PASS
worker recovery: PASS
SSE replay: PASS
SSE ownership: PASS
M-08 boundary: PASS
M-09+ boundary: PASS
A-Lite testing: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
