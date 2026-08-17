"""M-12 集中验证/去重/抽样策略默认值。禁止散落 magic numbers（D-047 原则）。

SYSTEM_DERIVED 例外必须程序可审计：字段名命中此集合时，才允许无网页
FieldEvidence 仍进入 PASSED（例如采集时间/source URL/内部 ID）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class ValidationSettings(BaseModel):
    model_config = _STRICT

    validation_version: str = "m12.1"
    system_derived_fields: frozenset[str] = frozenset()  # 显式白名单，默认空
    dedupe_min_similarity: float = 0.92  # deterministic fuzzy merge threshold
    approx_dedupe_max_candidates: int = 20  # LLM candidate pair 上限
    saturation_batch_window: int = 3  # 探索饱和：最近 N batch
    saturation_new_unique_threshold: float = 0.0  # 新增 unique 率低于此值即饱和
    min_qualified_records_for_saturation: int = 1
    max_search_rounds: int = 3  # 探索/混合 CONTINUE 的受控重规划上限（D-013 有界重试）
    sample_size_per_stratum: int = 5
    max_batch: int = 50
