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
    # M-08 plan-driven fields（默认 None，兼容 M-07 fixture 单元）
    node_id: str | None = None
    node_type: str | None = None
    definition_version: str | None = None
    parameters: dict | None = None
    requires_approval: bool = False
    approval_action_type: str | None = None
    approval_target: str | None = None
    approval_parameters: dict | None = None
    credential_ref: str | None = None  # 脱敏凭据引用（非明文）
    # M-16：来自 NodeDefinition.resource_class，用于确定性 TaskQueue 路由与 pool 准入。
    resource_class: str | None = None


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
    status: str = "OK"  # OK | NODE_EXECUTOR_UNAVAILABLE
    error_code: str | None = None


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    raise NotImplementedError("M-08 计划调度接入后由真实实现注册；M-07 测试用 fixture 覆盖")


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    raise NotImplementedError("M-08 计划调度接入后由真实实现注册；M-07 测试用 fixture 覆盖")
