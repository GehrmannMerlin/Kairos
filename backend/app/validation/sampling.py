"""M-12 分层抽样（D-014 抽样规则 / 模块需求 36-38）。

按 source / extraction method / rule version / confidence band 分层，每层 hash-based
确定性选取（stable_fingerprint(record_id, policy_version) 排序取前 k）。同 policy/version
→ 稳定 sample，不依赖 ORDER BY random()（模块需求 37）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.idempotency import stable_fingerprint

_STRICT = ConfigDict(extra="forbid")


class SamplingPolicy(BaseModel):
    model_config = _STRICT

    strata: list[str] = ["source", "extraction_method", "rule_version", "confidence_band"]
    sample_size_per_stratum: int = 5
    policy_version: str = "m12.1"


def _confidence_band(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.6:
        return "medium"
    return "low"


class StratifiedSampler:
    def __init__(self, policy: SamplingPolicy | None = None) -> None:
        self._policy = policy or SamplingPolicy()

    def select(self, records: list[Any], strata_facts: dict[int, dict]) -> tuple[list[dict], str]:
        """records: 已分区 Record；strata_facts: {record_id: {source, method, rule_version,
        confidence}}。返回 (sample_refs, plan_fingerprint)。每层 key = 四维分层组合，
        hash 确定性取前 k。
        """
        strata: dict[str, list[int]] = {}
        for rec in records:
            facts = strata_facts.get(rec.id, {})
            key = (
                str(facts.get("source") or "unknown"),
                str(facts.get("method") or "unknown"),
                str(facts.get("rule_version") or "none"),
                _confidence_band(facts.get("confidence")),
            )
            strata.setdefault(str(key), []).append(rec.id)
        sample: list[dict] = []
        for stratum_key, ids in sorted(strata.items()):
            chosen = sorted(
                ids, key=lambda rid: stable_fingerprint(rid, self._policy.policy_version)
            )
            for rid in chosen[: self._policy.sample_size_per_stratum]:
                sample.append({"record_id": rid, "stratum": stratum_key})
        plan_fingerprint = stable_fingerprint(
            "sampling", self._policy.policy_version, sorted(strata.keys())
        )
        return sample, plan_fingerprint


__all__ = ["SamplingPolicy", "StratifiedSampler"]
