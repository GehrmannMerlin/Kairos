"""Deterministic final confidence（系统值，不做 ML calibration）。

最终置信度结合 extraction method + schema validation + evidence grounding +
LLM uncertainty（三十一），简单确定性 blend；绝不直接采用 LLM 自报值。
"""

from __future__ import annotations

from app.extraction.contracts import ExtractorMethod

_METHOD_BASE = {
    ExtractorMethod.JSON_LD: 0.95,
    ExtractorMethod.META: 0.90,
    ExtractorMethod.TABLE: 0.85,
    ExtractorMethod.CSS: 0.88,
    ExtractorMethod.XPATH: 0.88,
    ExtractorMethod.RULE: 0.90,
    ExtractorMethod.LLM: 0.55,
}


def final_confidence(
    method: ExtractorMethod,
    *,
    schema_valid: bool = True,
    grounded: bool = True,
    llm_confidence: float = 0.0,
) -> float:
    base = _METHOD_BASE.get(method, 0.5)
    if not schema_valid or not grounded:
        base *= 0.4
    if method == ExtractorMethod.LLM:
        base = base * 0.5 + min(max(llm_confidence, 0.0), 1.0) * 0.5
    return round(min(base, 1.0), 3)
