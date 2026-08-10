"""Canonical transition matrix + allowed_actions."""

from __future__ import annotations

import pytest
from app.domain.errors import IllegalTransitionError
from app.state.states import (
    NodeState,
    TaskState,
    allowed_node_actions,
    allowed_task_actions,
    assert_node_transition,
    assert_task_transition,
)

TASK_OK = [
    (TaskState.DRAFT, "submit", TaskState.QUEUED),
    (TaskState.QUEUED, "start", TaskState.RUNNING),
    (TaskState.RUNNING, "pause", TaskState.PAUSING),
    (TaskState.PAUSING, "cancel", TaskState.CANCELLING),
    (TaskState.PAUSED, "resume", TaskState.RUNNING),
    (TaskState.RUNNING, "cancel", TaskState.CANCELLING),
    (TaskState.CANCELLING, "cancel", TaskState.CANCELLING),
    (TaskState.RUNNING, "complete", TaskState.COMPLETED),
    (TaskState.RUNNING, "mark_partial", TaskState.PARTIALLY_COMPLETED),
    (TaskState.RUNNING, "fail", TaskState.FAILED),
    (TaskState.QUEUED, "cancel", TaskState.CANCELLED),
    (TaskState.DRAFT, "delete", TaskState.DELETED),
    (TaskState.COMPLETED, "delete", TaskState.DELETED),
    (TaskState.FAILED, "delete", TaskState.DELETED),
    (TaskState.DELETED, "restore", TaskState.DRAFT),
]

TASK_BAD = [
    (TaskState.RUNNING, "delete", TaskState.DELETED),
    (TaskState.RUNNING, "resume", TaskState.RUNNING),
    (TaskState.DRAFT, "start", TaskState.RUNNING),
    (TaskState.DELETED, "submit", TaskState.QUEUED),
    (TaskState.COMPLETED, "start", TaskState.RUNNING),
]


@pytest.mark.parametrize(("state", "cmd", "next"), TASK_OK)
def test_task_legal_transitions(state: TaskState, cmd: str, next: TaskState) -> None:
    assert assert_task_transition(state, cmd) == next


@pytest.mark.parametrize(("state", "cmd", "next"), TASK_BAD)
def test_task_illegal_transitions(state: TaskState, cmd: str, next: TaskState) -> None:
    with pytest.raises(IllegalTransitionError):
        assert_task_transition(state, cmd)


def test_task_allowed_actions_consistent() -> None:
    for state in TaskState:
        for action in allowed_task_actions(state):
            assert_task_transition(state, action)


def test_node_allowed_actions_consistent() -> None:
    for state in NodeState:
        for action in allowed_node_actions(state):
            assert_node_transition(state, action)


def test_running_task_cannot_delete() -> None:
    assert "delete" not in allowed_task_actions(TaskState.RUNNING)
    assert "delete" not in allowed_task_actions(TaskState.PAUSING)
    assert "delete" not in allowed_task_actions(TaskState.CANCELLING)


def test_node_retry_loop() -> None:
    assert assert_node_transition(NodeState.RUNNING, "wait_retry") == NodeState.WAITING_RETRY
    assert assert_node_transition(NodeState.WAITING_RETRY, "retry") == NodeState.READY
