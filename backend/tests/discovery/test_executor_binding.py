"""M-09 Task 8: discovery executors 注册绑定（无栈可运行）。

install_discovery_executors() 把 SourceSearch/AccessRulesCheck/LinkDiscovery 注册进
M-08 NODE_EXECUTORS；execute_safe_unit 通过该注册表 dispatch 真实 executor。
"""

from __future__ import annotations

from app.discovery.executors import install_discovery_executors
from app.plan.executors import NODE_EXECUTORS, get_node_executor
from app.plan.nodes import NodeType


def test_discovery_executors_registered() -> None:
    install_discovery_executors()
    assert get_node_executor(NodeType.SOURCE_SEARCH.value) is not None
    assert get_node_executor(NodeType.ACCESS_RULES_CHECK.value) is not None
    assert get_node_executor(NodeType.LINK_DISCOVERY.value) is not None
    assert NodeType.SOURCE_SEARCH in NODE_EXECUTORS
    assert NodeType.ACCESS_RULES_CHECK in NODE_EXECUTORS
    assert NodeType.LINK_DISCOVERY in NODE_EXECUTORS
