"""Node executor registry（M-08）。

生产注册表保持空；M-09～M-12 将真实 Activity 挂入。测试/Staging fixture executor
通过 ``register_node_executor`` 注册，仅 test worker / plan_fixture_mode 下可用，
Production 强制关闭（由部署环境配置覆盖）。生产运行遇到无实现 Node →
NODE_EXECUTOR_UNAVAILABLE，不用 fake execution 冒充 Production 能力。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.plan.nodes import NodeType

NODE_EXECUTORS: dict[NodeType, Callable[..., Awaitable[Any]]] = {}


def register_node_executor(node_type: NodeType, fn: Callable[..., Awaitable[Any]]) -> None:
    NODE_EXECUTORS[node_type] = fn


def get_node_executor(node_type: str | None):
    if node_type is None:
        return None
    try:
        return NODE_EXECUTORS.get(NodeType(node_type))
    except ValueError:
        return None


def is_registered(node_type: str | None) -> bool:
    return get_node_executor(node_type) is not None
