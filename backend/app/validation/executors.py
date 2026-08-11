"""M-12 executor 注册（M-08 NODE_EXECUTORS 绑定）：DEDUPLICATE + VALIDATE。

真实能力不是 fixture；与 M-09/M-10/M-11 executor 注册方式一致。worker.py 启动时调用。
"""

from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_validation_executors() -> None:
    from app.infra.deps import get_session_factory

    async def _dedupe(unit):
        session = get_session_factory()()
        try:
            from app.validation.executor import DeduplicateNodeExecutor

            return await DeduplicateNodeExecutor(session).execute(unit)
        finally:
            session.close()

    async def _validate(unit):
        session = get_session_factory()()
        try:
            from app.validation.executor import ValidateNodeExecutor

            return await ValidateNodeExecutor(session).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.DEDUPLICATE, _dedupe)
    register_node_executor(NodeType.VALIDATE, _validate)


__all__ = ["install_validation_executors"]
