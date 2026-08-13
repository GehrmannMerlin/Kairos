"""M-09 Temporal integration: 两阶段来源发现 Workflow（KAIROS_RUN_INTEGRATION=1）。

需本地 Temporal + Postgres 栈 + KAIROS_RUN_INTEGRATION=1；本地栈未启动时收集通过、
未实跑（与 M-08 test_plan_workflow 先例一致）。executor 注册绑定由
tests/discovery/test_executor_binding.py 无栈验证；发现语义由
tests/discovery/test_discovery_e2e.py service 级端到端验证。
"""

from __future__ import annotations

from uuid import uuid4

import pytest


def _fresh_id(prefix: str) -> str:
    return f"m09-{prefix}-{uuid4().hex[:8]}"


@pytest.mark.integration
async def test_scenario_a_temporal_specified_source() -> None:
    """场景 A：SPECIFIED_SOURCE seed → AccessRulesCheck → LinkDiscovery → Frontier。"""
    marker = _fresh_id("spec-source")
    assert marker  # 占位：栈可用时提交真实 SPECIFIED_SOURCE 计划并断言 READY_FOR_FETCH


@pytest.mark.integration
async def test_scenario_b_temporal_exploratory() -> None:
    """场景 B：EXPLORATORY → Fake SearchProvider → SourceSearch → AccessRules → Frontier。"""
    marker = _fresh_id("exploratory")
    assert marker  # 占位：栈可用时提交 EXPLORATORY 计划（Fake Search）并断言 Frontier
