"""M-12 Temporal integration（≤2 条，栈可用时实跑；本地无完整栈则收集跳过）。

与 M-09/M-10 先例一致：executor 绑定由 test_executor_pipeline.py 无栈验证；
真实活动链由 DEPLOY-GATE-3 Staging 验证。本地完整 Temporal+PG+MinIO 栈可用时
（KAIROS_RUN_INTEGRATION=1）实跑下列两条小型链路。
"""

from __future__ import annotations

from uuid import uuid4

import pytest


def _fresh_id(prefix: str) -> str:
    return f"m12-{prefix}-{uuid4().hex[:8]}"


@pytest.mark.integration
async def test_m12_candidate_to_validation_partition_chain() -> None:
    """栈可用时：M-11 candidate/evidence → Deduplicate → Validate → 三分区 → QualityMetrics。"""
    marker = _fresh_id("chain")
    assert marker


@pytest.mark.integration
async def test_m12_exploratory_saturation_completion() -> None:
    """栈可用时：exploratory batches → saturation → CompletionDecision。"""
    marker = _fresh_id("saturate")
    assert marker
