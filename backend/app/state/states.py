"""Canonical state vocabulary, transition matrix and allowed_actions (M-04).

Single source of truth for state semantics. The database stores
``TaskState.value`` / ``NodeState.value`` (uppercase). Never add a second name
for the same meaning.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import IllegalTransitionError


class TaskState(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class NodeState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# command -> (from_state, to_state). One source of truth for the matrix.
TASK_COMMANDS: dict[str, list[tuple[TaskState, TaskState]]] = {
    "submit": [(TaskState.DRAFT, TaskState.QUEUED)],
    "start": [(TaskState.QUEUED, TaskState.RUNNING)],
    "pause": [(TaskState.RUNNING, TaskState.PAUSING)],
    "resume": [
        (TaskState.PAUSED, TaskState.RUNNING),
        (TaskState.WAITING_APPROVAL, TaskState.RUNNING),
        (TaskState.WAITING_RESOURCE, TaskState.RUNNING),
    ],
    "cancel": [
        (TaskState.QUEUED, TaskState.CANCELLED),
        (TaskState.RUNNING, TaskState.CANCELLING),
        (TaskState.PAUSING, TaskState.CANCELLING),
        (TaskState.PAUSED, TaskState.CANCELLING),
        (TaskState.WAITING_APPROVAL, TaskState.CANCELLING),
        (TaskState.WAITING_RESOURCE, TaskState.CANCELLING),
        (TaskState.CANCELLING, TaskState.CANCELLING),
    ],
    "complete": [(TaskState.RUNNING, TaskState.COMPLETED)],
    "mark_partial": [(TaskState.RUNNING, TaskState.PARTIALLY_COMPLETED)],
    "fail": [(TaskState.RUNNING, TaskState.FAILED)],
    "mark_waiting_approval": [(TaskState.RUNNING, TaskState.WAITING_APPROVAL)],
    "mark_waiting_resource": [(TaskState.RUNNING, TaskState.WAITING_RESOURCE)],
    "delete": [
        (TaskState.DRAFT, TaskState.DELETED),
        (TaskState.QUEUED, TaskState.DELETED),
        (TaskState.PAUSED, TaskState.DELETED),
        (TaskState.WAITING_APPROVAL, TaskState.DELETED),
        (TaskState.WAITING_RESOURCE, TaskState.DELETED),
        (TaskState.CANCELLED, TaskState.DELETED),
        (TaskState.COMPLETED, TaskState.DELETED),
        (TaskState.PARTIALLY_COMPLETED, TaskState.DELETED),
        (TaskState.FAILED, TaskState.DELETED),
    ],
    "restore": [(TaskState.DELETED, TaskState.DRAFT)],
}

# 系统内部命令（仅 Workflow/Activity 在安全停止后调用），不出现在用户 allowed_actions。
TASK_SYSTEM_COMMANDS: dict[str, list[tuple[TaskState, TaskState]]] = {
    "mark_paused": [(TaskState.PAUSING, TaskState.PAUSED)],
    "mark_cancelled": [(TaskState.CANCELLING, TaskState.CANCELLED)],
}

NODE_COMMANDS: dict[str, list[tuple[NodeState, NodeState]]] = {
    "ready": [(NodeState.PENDING, NodeState.READY)],
    "dispatch": [
        (NodeState.READY, NodeState.RUNNING),
        (NodeState.WAITING_RESOURCE, NodeState.READY),
    ],
    "succeed": [(NodeState.RUNNING, NodeState.SUCCEEDED)],
    "fail": [(NodeState.RUNNING, NodeState.FAILED)],
    "wait_retry": [(NodeState.RUNNING, NodeState.WAITING_RETRY)],
    "retry": [(NodeState.WAITING_RETRY, NodeState.READY)],
    "wait_resource": [(NodeState.RUNNING, NodeState.WAITING_RESOURCE)],
    "requeue": [(NodeState.WAITING_RESOURCE, NodeState.READY)],
    "skip": [(NodeState.PENDING, NodeState.SKIPPED), (NodeState.READY, NodeState.SKIPPED)],
    "block": [(NodeState.PENDING, NodeState.BLOCKED)],
    "unblock": [(NodeState.BLOCKED, NodeState.READY)],
    "cancel": [
        (NodeState.PENDING, NodeState.CANCELLED),
        (NodeState.READY, NodeState.CANCELLED),
        (NodeState.RUNNING, NodeState.CANCELLED),
        (NodeState.WAITING_RETRY, NodeState.CANCELLED),
        (NodeState.WAITING_RESOURCE, NodeState.CANCELLED),
        (NodeState.BLOCKED, NodeState.CANCELLED),
    ],
}


def _resolve(commands: dict, kind: str, state: object, command: str):
    for from_state, to_state in commands.get(command, []):
        if from_state == state:
            return to_state
    raise IllegalTransitionError(f"{kind} 当前状态不允许执行 {command}")


def assert_task_transition(state: TaskState, command: str) -> TaskState:
    try:
        return _resolve(TASK_COMMANDS, "任务", state, command)
    except IllegalTransitionError:
        return _resolve(TASK_SYSTEM_COMMANDS, "任务", state, command)


def assert_node_transition(state: NodeState, command: str) -> NodeState:
    return _resolve(NODE_COMMANDS, "节点", state, command)


def allowed_task_actions(state: TaskState) -> list[str]:
    return [cmd for cmd, pairs in TASK_COMMANDS.items() if any(f == state for f, _ in pairs)]


def allowed_node_actions(state: NodeState) -> list[str]:
    return [cmd for cmd, pairs in NODE_COMMANDS.items() if any(f == state for f, _ in pairs)]
