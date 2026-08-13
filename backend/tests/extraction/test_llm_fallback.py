"""Fixture C unit: SemanticExtractionAgent typed fallback via FakeInference.

Proves: typed result passes; evidence quote is grounded; invalid LLM outputs
(wrong type / unknown field / missing evidence / hallucinated quote) are rejected
by the pipeline gates (grounding + schema validation).
"""
from __future__ import annotations

import json

import pytest
from app.domain.spec import FieldSpec, FieldType
from app.extraction.confidence import final_confidence
from app.extraction.contracts import ExtractionCandidate, ExtractorMethod
from app.extraction.grounding import evidence_is_grounded
from app.extraction.llm import (
    SemanticExtractionAgent,
    SemanticExtractionInput,
    SemanticExtractionResult,
)
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.providers.inference import InferenceResult, ModelInferenceClient
from app.providers.protocol import ResolvedModel

SITE_TEXT = (
    "深圳市南山科技有限公司位于深圳市南山区科技园。"
    "公司主营工业自动化设备。官网是 https://nanshan.example.com。联系电话 0755-11112222。"
)

SPEC_FIELDS = {
    "公司名": FieldSpec(name="公司名", type=FieldType.TEXT),
    "官网": FieldSpec(name="官网", type=FieldType.URL),
    "主营产品": FieldSpec(name="主营产品", type=FieldType.TEXT),
}


class FakeInference(ModelInferenceClient):
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.invocation_count = 0
        self.system_seen: str | None = None
        self.user_seen: str | None = None

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        self.invocation_count += 1
        self.system_seen = system
        self.user_seen = user
        return InferenceResult(
            text=json.dumps(self._payload, ensure_ascii=False),
            provider_type="deepseek",
            duration_ms=1,
        )


RESOLVED = ResolvedModel(
    provider_type="deepseek", model_name="placeholder", base_url=None, credential_version_id=None
)


def _input(unresolved: list[str] | None = None, text: str = SITE_TEXT) -> SemanticExtractionInput:
    return SemanticExtractionInput(
        schema_version="m11.1",
        fields=[
            {"name": "公司名", "type": "text", "required": True},
            {"name": "官网", "type": "url", "required": True},
            {"name": "主营产品", "type": "text", "required": False},
        ],
        unresolved_fields=unresolved or ["主营产品"],
        known_candidates=[],
        readable_text=text,
        source_url="http://fixture.test/",
        snapshot_id=1,
        run_id=1,
    )


def _llm_candidate(cand) -> ExtractionCandidate:
    return ExtractionCandidate(
        field_name=cand.field_name,
        raw_value=cand.value,
        method=ExtractorMethod.LLM,
        confidence=cand.confidence,
        extractor_version="m11.1",
    )


def _accept(cand, text: str) -> tuple[bool, str]:
    """Apply the pipeline gates exactly as ExtractionPipeline does (grounding + schema)."""
    if not cand.value:
        return False, "LLM_MISSING_VALUE"
    if not evidence_is_grounded(cand.evidence_quote, text):
        return False, "EVIDENCE_NOT_GROUNDED"
    field = SPEC_FIELDS.get(cand.field_name)
    if field is None:
        return False, "LLM_UNKNOWN_FIELD"
    issue = ExtractionSchemaValidator().validate(_llm_candidate(cand), field)
    if issue is not None:
        return False, issue.code
    return True, "VALID"


@pytest.mark.asyncio
async def test_agent_typed_result_and_grounded_evidence():
    fake = FakeInference(
        {
            "fields": [
                {
                    "field_name": "主营产品",
                    "value": "工业自动化设备",
                    "evidence_quote": "公司主营工业自动化设备",
                    "confidence": 0.8,
                    "proposed_selector": "div.business",
                }
            ]
        }
    )
    agent = SemanticExtractionAgent(inference=fake)
    result = await agent.extract(_input(), RESOLVED, api_key="secret")
    assert isinstance(result, SemanticExtractionResult)
    cand = result.fields[0]
    assert cand.field_name == "主营产品"
    assert cand.value == "工业自动化设备"
    assert evidence_is_grounded(cand.evidence_quote, SITE_TEXT)
    # 系统置信度是确定性 blend，绝不直接采用 LLM 自报 0.8
    conf = final_confidence(
        ExtractorMethod.LLM, schema_valid=True, grounded=True, llm_confidence=cand.confidence
    )
    assert 0.3 < conf < 1.0
    assert fake.invocation_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,expected_reject",
    [
        (
            {
                "fields": [
                    {
                        "field_name": "主营产品",
                        "value": "工业自动化设备",
                        "evidence_quote": "不存在的内容",
                        "confidence": 0.9,
                    }
                ]
            },
            "EVIDENCE_NOT_GROUNDED",
        ),
        (
            {
                "fields": [
                    {
                        "field_name": "未知字段",
                        "value": "x",
                        "evidence_quote": "公司主营工业自动化设备",
                        "confidence": 0.9,
                    }
                ]
            },
            "LLM_UNKNOWN_FIELD",
        ),
        (
            {
                "fields": [
                    {
                        "field_name": "主营产品",
                        "value": "工业自动化设备",
                        "evidence_quote": "",
                        "confidence": 0.9,
                    }
                ]
            },
            "EVIDENCE_NOT_GROUNDED",
        ),
        (
            {
                "fields": [
                    {
                        "field_name": "官网",
                        "value": "not-a-url",
                        "evidence_quote": "官网是 https://nanshan.example.com",
                        "confidence": 0.9,
                    }
                ]
            },
            "SCHEMA_TYPE_URL",
        ),
    ],
)
async def test_invalid_llm_output_rejected(payload, expected_reject):
    fake = FakeInference(payload)
    agent = SemanticExtractionAgent(inference=fake)
    result = await agent.extract(
        _input(unresolved=["主营产品", "官网"]), RESOLVED, api_key="k"
    )
    accepted, reason = _accept(result.fields[0], SITE_TEXT)
    assert accepted is False
    assert reason == expected_reject
