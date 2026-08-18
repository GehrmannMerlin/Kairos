"""TaskWorkflow MORE_PENDING 小批次重跑（M-11）— 直接调用 Workflow.run + mock activities。

不依赖 Temporal 服务器（复用 test_task_workflow.py 的 monkeypatch 手法，但不挂 integration
marker，默认 CI 可跑）。验证：EXTRACT 单元返回 MORE_PENDING 时不推进 index、重取同一单元、
每个小批提交独立 batch_identity checkpoint，最终 OK 后推进并 COMPLETED。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit, FetchUnitResult
from app.workflows import task_workflow
from app.workflows.task_workflow import TaskWorkflow, TaskWorkflowInput


def _unit(index: int, node_type: str = "extract") -> ExecutionUnit:
    return ExecutionUnit(
        run_id=3,
        index=index,
        unit_type=node_type,
        input_fingerprint=f"fp-3-{index}",
        node_id=f"{node_type}-1",
        node_type=node_type,
        resource_class="core",
        timeout_seconds=200,
    )


@pytest.mark.asyncio
async def test_more_pending_reruns_unit_then_advances(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []
    state = {"fetch_count": 0, "extract_round": 0}

    async def execute_activity(activity_fn: Any, activity_input: Any, **_kwargs: Any) -> Any:
        name = activity_fn.__name__
        calls.append((name, activity_input))
        if name == "ensure_run_started":
            return SimpleNamespace(started=True)
        if name == "heartbeat_task_slot":
            return None
        if name == "fetch_next_execution_unit":
            state["fetch_count"] += 1
            if state["fetch_count"] <= 2:
                return FetchUnitResult(unit=_unit(1))
            return FetchUnitResult(unit=None)
        if name == "execute_safe_unit":
            assert activity_input.unit.timeout_seconds == 200
            state["extract_round"] += 1
            round_no = state["extract_round"]
            if round_no == 1:
                return ExecuteUnitResult(
                    unit_index=1,
                    status="MORE_PENDING",
                    committed_refs={
                        "extracted": 5,
                        "failed": 0,
                        "remaining": 3,
                        "batch_identity": f"extract-3-1-10-{round_no}",
                    },
                )
            return ExecuteUnitResult(
                unit_index=1,
                status="OK",
                committed_refs={
                    "extracted": 3,
                    "failed": 0,
                    "remaining": 0,
                    "batch_identity": f"extract-3-1-15-{round_no}",
                },
            )
        if name == "commit_checkpoint":
            assert activity_input.batch_identity in {
                "extract-3-1-10-1",
                "extract-3-1-15-2",
            }
            return SimpleNamespace(checkpoint_id=1, reused=False)
        if name == "resolve_completion":
            return SimpleNamespace(
                status="NORMAL_COMPLETED",
                partial=False,
                outcome="COMPLETED",
                failure_code=None,
                continue_hints={},
            )
        if name == "complete_run":
            return None
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr(task_workflow.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(task_workflow, "workflow_queue_override", lambda _resource_class: None)

    result = await TaskWorkflow().run(
        TaskWorkflowInput(task_id=1, user_id=2, run_id=3, spec_version=1, plan_version=1)
    )

    assert result.final_state == "COMPLETED"
    assert state["extract_round"] == 2
    # MORE_PENDING 不推进 index → 同一单元被 fetch 两次
    fetches = [value for name, value in calls if name == "fetch_next_execution_unit"]
    assert len(fetches) == 3  # 两批 + 末尾空
    assert fetches[0].after_index == fetches[1].after_index == 0
    # 每个小批独立 checkpoint 身份
    checkpoints = [value for name, value in calls if name == "commit_checkpoint"]
    assert len(checkpoints) == 2
    assert checkpoints[0].batch_identity != checkpoints[1].batch_identity


@pytest.mark.asyncio
async def test_extract_activity_uses_node_definition_timeout(monkeypatch) -> None:
    """M-11：execute_safe_unit 的 start_to_close 使用 NodeDefinition.timeout_seconds。"""
    seen: dict[str, Any] = {}
    state = {"fetch_count": 0}

    async def execute_activity(activity_fn: Any, activity_input: Any, **_kwargs: Any) -> Any:
        name = activity_fn.__name__
        if name == "ensure_run_started":
            return SimpleNamespace(started=True)
        if name == "heartbeat_task_slot":
            return None
        if name == "fetch_next_execution_unit":
            state["fetch_count"] += 1
            if state["fetch_count"] == 1:
                return FetchUnitResult(unit=_unit(1))
            return FetchUnitResult(unit=None)
        if name == "execute_safe_unit":
            timeout = _kwargs.get("start_to_close_timeout")
            assert isinstance(timeout, timedelta)
            seen["timeout"] = timeout
            return ExecuteUnitResult(unit_index=1, committed_refs={}, status="OK")
        if name == "commit_checkpoint":
            return SimpleNamespace(checkpoint_id=1, reused=False)
        if name == "resolve_completion":
            return SimpleNamespace(
                status="NORMAL_COMPLETED",
                partial=False,
                outcome="COMPLETED",
                failure_code=None,
                continue_hints={},
            )
        if name == "complete_run":
            return None
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr(task_workflow.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(task_workflow, "workflow_queue_override", lambda _resource_class: None)

    await TaskWorkflow().run(
        TaskWorkflowInput(task_id=1, user_id=2, run_id=3, spec_version=1, plan_version=1)
    )

    assert seen["timeout"].total_seconds() == 200
