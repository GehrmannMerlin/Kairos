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
