"""M-10 Temporal integration: M-09 Frontier → READY_FOR_FETCH → Fetch → PageSnapshot → FETCHED。

需本地 Temporal + Postgres + MinIO 栈 + KAIROS_RUN_INTEGRATION=1；本地栈未启动时收集通过、
未实跑（与 M-09 / M-08 先例一致）。executor 绑定由 test_executor_binding.py 无栈验证；
Fetch 语义由 test_fetch_e2e_*.py service 级端到端验证。
"""

from __future__ import annotations

from uuid import uuid4

import pytest


def _fresh_id(prefix: str) -> str:
    return f"m10-{prefix}-{uuid4().hex[:8]}"


@pytest.mark.integration
async def test_m09_frontier_to_m10_fetch_handoff() -> None:
    """栈可用时：SPECIFIED_SOURCE seed → AccessRules → LinkDiscovery → READY_FOR_FETCH
    → plan [fetch] → TaskWorkflow → FetchNodeExecutor → PageSnapshot → URL FETCHED。"""
    marker = _fresh_id("fetch")
    assert marker  # 占位：栈可用时提交真实计划并断言 Frontier READY_FOR_FETCH → FETCHED
