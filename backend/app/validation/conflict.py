"""M-12 跨来源冲突确定性裁决（D-014 冲突规则 / 模块需求 23-27）。

裁决顺序（模块需求 24）：source priority → evidence strength → method reliability →
rule validation → snapshot/fetch time → confidence。无法可靠裁决 → NEEDS_REVIEW，
保留全部候选（不静默选一个/取第一个/取最新 row/LLM 猜）。最终 Record 即使确定
final value 仍保留 rejected candidate refs 供 M-13 审计。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from app.extraction.contracts import ExtractorMethod

_STRICT = ConfigDict(extra="forbid")

CONFLICT_POLICY_VERSION = "m12.1"

# extraction method reliability（structured > rule > llm）
_METHOD_RANK = {
    ExtractorMethod.JSON_LD: 6,
    ExtractorMethod.META: 5,
    ExtractorMethod.TABLE: 5,
    ExtractorMethod.CSS: 4,
    ExtractorMethod.XPATH: 4,
    ExtractorMethod.RULE: 3,
    ExtractorMethod.LLM: 1,
}

_MIN_UTC = datetime.min.replace(tzinfo=UTC)


class ConflictCandidateValue(BaseModel):
    model_config = _STRICT

    record_id: int
    value: str
    evidence_strength: float = 0.0  # has evidence + confidence 加权
    source_priority: int = 0  # priority_for(DiscoverySource, rank) 或任务自定义 policy
    method: str = "llm"
    rule_validated: bool = False
    fetched_at: datetime | None = None
    confidence: float = 0.0


class ConflictResolution(BaseModel):
    model_config = _STRICT

    decision: str  # resolved | needs_review
    policy_version: str = CONFLICT_POLICY_VERSION
    chosen_value: str | None = None
    chosen_record_id: int | None = None
    rejected_refs: list[dict] = []  # [{record_id, value, reason}]
    reason: str = ""


class ConflictResolver:
    """确定性裁决：逐层比较，任一决定性领先即 resolved；否则 needs_review。"""

    def resolve(
        self, field_name: str, candidates: list[ConflictCandidateValue]
    ) -> ConflictResolution:
        if len(candidates) < 2:
            return ConflictResolution(
                decision="resolved",
                chosen_value=candidates[0].value if candidates else None,
                chosen_record_id=candidates[0].record_id if candidates else None,
                reason="single_source",
            )
        ranked = sorted(
            candidates,
            key=lambda c: (
                c.source_priority,  # 1. source priority
                c.evidence_strength,  # 2. evidence strength
                _METHOD_RANK.get(ExtractorMethod(c.method), 0),  # 3. method reliability
                c.rule_validated,  # 4. rule validation status
                c.fetched_at or _MIN_UTC,  # 5. snapshot/fetch time
                c.confidence,  # 6. confidence
            ),
            reverse=True,
        )
        top, second = ranked[0], ranked[1]
        if (
            top.source_priority > second.source_priority
            or top.evidence_strength > second.evidence_strength + 1e-9
            or _METHOD_RANK.get(ExtractorMethod(top.method), 0)
            > _METHOD_RANK.get(ExtractorMethod(second.method), 0)
            or top.rule_validated
            and not second.rule_validated
        ):
            decision = "resolved"
        else:
            decision = "needs_review"
        if decision == "resolved":
            rejected = [
                {"record_id": c.record_id, "value": c.value, "reason": "lower_priority"}
                for c in ranked[1:]
            ]
            return ConflictResolution(
                decision="resolved",
                chosen_value=top.value,
                chosen_record_id=top.record_id,
                rejected_refs=rejected,
                reason=f"deterministic_policy:{CONFLICT_POLICY_VERSION}",
            )
        # 无法裁决：保留全部候选，不静默选值
        return ConflictResolution(
            decision="needs_review",
            reason="tie_not_resolvable_deterministically",
            rejected_refs=[
                {"record_id": c.record_id, "value": c.value, "reason": "tie"} for c in ranked
            ],
        )


__all__ = [
    "ConflictCandidateValue",
    "ConflictResolution",
    "ConflictResolver",
    "CONFLICT_POLICY_VERSION",
]
