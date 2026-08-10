"""TaskWorkflowStarter — Run 创建 + Workflow 启动 + M-08 plan seam。

run_id 由命令层在 Workflow 启动前生成稳定 ID；Workflow 第一步 ensure_run_started
Activity 幂等激活（Spec 冻结校验 + QUEUED->RUNNING + DomainEvent/Outbox）。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio.client import Client

from app.config import Settings, get_settings
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
                user_id=user_id,
                task_id=task_id,
                spec_version=spec_version,
                plan_version=plan_version,
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
