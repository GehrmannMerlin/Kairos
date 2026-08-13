"""真实 M-09 executor 注册（M-08 NODE_EXECUTORS 绑定）。

Worker 启动时调用 ``install_discovery_executors()``；真实 executor 在 Temporal
Activity（execute_safe_unit）内运行，所有 HTTP 都在 executor 中完成，Workflow
不做网络请求。Production 与测试 worker 都启用（这是真实能力，不是 fixture）。
"""

from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_discovery_executors() -> None:
    from app.discovery.access_rules import AccessRulesService
    from app.discovery.link_discovery import LinkDiscoveryService
    from app.discovery.source_search import SearchService
    from app.infra.deps import get_session_factory

    async def _source_search(unit):
        session = get_session_factory()()
        try:
            return await SearchService(session).execute(unit)
        finally:
            session.close()

    async def _access_rules(unit):
        session = get_session_factory()()
        try:
            return await AccessRulesService(session).execute(unit)
        finally:
            session.close()

    async def _link_discovery(unit):
        session = get_session_factory()()
        try:
            return await LinkDiscoveryService(session).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.SOURCE_SEARCH, _source_search)
    register_node_executor(NodeType.ACCESS_RULES_CHECK, _access_rules)
    register_node_executor(NodeType.LINK_DISCOVERY, _link_discovery)
