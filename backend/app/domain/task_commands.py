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

    def _run(
        self,
        *,
        user_id: int,
        task_id: int,
        expected_version: int,
        command: str,
        idempotency_key: str | None,
        reason: str | None,
    ) -> TaskCommandResult:
        # 幂等语义只绑定“哪条命令作用于哪个任务”，不绑定 expected_version：
        # 乐观锁版本由 transition_task 单独校验；同一 key 的重试即使带了新读到的
        # 版本号，仍是同一条逻辑请求（replay），不应误判为 payload 冲突。
        payload = {"command": command, "task_id": task_id}
        op = f"task.{command}"
        if idempotency_key:
            replay = self._idem.find_replay(
                self._db,
                user_id=user_id,
                operation=op,
                client_key=idempotency_key,
                payload=payload,
            )
            if replay is not None:
                task = TaskRepository(self._db).get_owned(user_id, task_id)
                return TaskCommandResult(command=command, state=task.state, version=task.version)
        DomainService(TaskRepository(self._db)).transition_task(
            user_id=user_id,
            task_id=task_id,
            command=command,
            expected_version=expected_version,
            reason=reason,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        if idempotency_key:
            self._idem.record(
                self._db,
                user_id=user_id,
                operation=op,
                client_key=idempotency_key,
                payload=payload,
                result_ref=("task", task.id),
            )
        return TaskCommandResult(command=command, state=task.state, version=task.version)

    def pause_task(
        self,
        *,
        user_id: int,
        task_id: int,
        expected_version: int,
        idempotency_key: str | None = None,
        reason: str | None = None,
    ) -> TaskCommandResult:
        return self._run(
            user_id=user_id,
            task_id=task_id,
            expected_version=expected_version,
            command="pause",
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def resume_task(
        self,
        *,
        user_id: int,
        task_id: int,
        expected_version: int,
        idempotency_key: str | None = None,
        reason: str | None = None,
    ) -> TaskCommandResult:
        return self._run(
            user_id=user_id,
            task_id=task_id,
            expected_version=expected_version,
            command="resume",
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def cancel_task(
        self,
        *,
        user_id: int,
        task_id: int,
        expected_version: int,
        idempotency_key: str | None = None,
        reason: str | None = None,
    ) -> TaskCommandResult:
        return self._run(
            user_id=user_id,
            task_id=task_id,
            expected_version=expected_version,
            command="cancel",
            idempotency_key=idempotency_key,
            reason=reason,
        )
