"""Shared inference factory routes intent policy and caller settings to the wire."""

from __future__ import annotations

import json

import pytest
from app.agents.goal_understanding import GoalInput, GoalUnderstandingAgent
from app.agents.plan_service import PlanGenerationService
from app.config import Settings
from app.domain.task_types import TaskType
from app.extraction.llm import SemanticExtractionAgent, SemanticExtractionInput
from app.providers.inference_factory import build_inference_client
from app.providers.inference_policy import InferenceIntent
from app.providers.protocol import ResolvedModel
from tests.providers.fake_transport import FakeHttpClient


def _response(text: str = '{"ok":true}') -> dict:
    return {"choices": [{"message": {"content": text}}]}


async def _call_openai_compatible(
    *, intent: InferenceIntent, provider_type: str, base_url: str
) -> dict:
    fake = FakeHttpClient(200, _response())
    client = build_inference_client(
        intent=intent,
        settings=Settings(provider_inference_timeout_seconds=7.25),
        http=fake,
    )
    await client.generate(
        resolved=ResolvedModel(provider_type, "fixture-model", base_url, None),
        api_key="test-only-key",
        system="system",
        user="user",
    )
    return fake.calls[0]


@pytest.mark.asyncio
async def test_deepseek_plan_factory_applies_structured_policy_to_wire_body() -> None:
    """Dropping intent resolution must remove a required incident-fix field."""
    call = await _call_openai_compatible(
        intent=InferenceIntent.PLAN_STRUCTURED,
        provider_type="deepseek",
        base_url="https://api.deepseek.com/v1",
    )

    assert call["body"]["response_format"] == {"type": "json_object"}
    assert call["body"]["thinking"] == {"type": "disabled"}
    assert call["body"]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_deepseek_goal_factory_preserves_goal_request_behavior() -> None:
    """Leaking Plan tuning into Goal Understanding must fail this test."""
    call = await _call_openai_compatible(
        intent=InferenceIntent.GOAL_EXTRACTION,
        provider_type="deepseek",
        base_url="https://api.deepseek.com/v1",
    )

    assert call["body"]["response_format"] == {"type": "json_object"}
    assert "thinking" not in call["body"]
    assert "max_tokens" not in call["body"]


@pytest.mark.asyncio
async def test_custom_compatible_factory_omits_deepseek_private_fields() -> None:
    """Provider-compatible transport must not imply DeepSeek-private semantics."""
    call = await _call_openai_compatible(
        intent=InferenceIntent.CUSTOM_AGENT,
        provider_type="custom_openai_compatible",
        base_url="https://compatible.example/v1",
    )

    assert call["body"]["response_format"] == {"type": "json_object"}
    assert "thinking" not in call["body"]
    assert "max_tokens" not in call["body"]


@pytest.mark.asyncio
async def test_plan_service_carries_supplied_settings_through_factory() -> None:
    """Falling back to global settings must change the observed wire timeout."""
    graph = {
        "schema_version": "m08.1",
        "task_id": 1,
        "spec_version": 1,
        "task_type": "SPECIFIED_SOURCE",
        "nodes": [],
        "edges": [],
    }
    fake = FakeHttpClient(200, _response(json.dumps(graph)))
    settings = Settings(provider_inference_timeout_seconds=7.25)
    service = PlanGenerationService(settings=settings, http=fake)
    spec = {
        "task_type": "SPECIFIED_SOURCE",
        "goal": "fixture",
        "fields": [],
        "source_scope": {"mode": "SPECIFIED_SOURCE", "seed_urls": [], "source_hints": []},
    }
    inp = service.build_input(spec, TaskType.SPECIFIED_SOURCE)

    await service._run_with_graph(
        spec,
        inp,
        ResolvedModel("deepseek", "deepseek-chat", "https://api.deepseek.com/v1", None),
    )

    assert fake.calls[0]["timeout_seconds"] == 7.25
    assert fake.calls[0]["body"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_goal_agent_default_client_binds_goal_intent() -> None:
    """Changing Goal's default intent to Plan must leak thinking and fail."""
    result = {
        "task_type": "EXPLORATORY",
        "goal": "采集企业信息",
        "fields": [],
        "source_scope": {"mode": "EXPLORATORY", "seed_urls": [], "source_hints": []},
        "completion_conditions": [],
        "confidence": 0.9,
        "clarification_required": False,
    }
    fake = FakeHttpClient(200, _response(json.dumps(result, ensure_ascii=False)))
    agent = GoalUnderstandingAgent(
        settings=Settings(provider_inference_timeout_seconds=6.5),
        http=fake,
    )

    await agent.understand(
        goal_input=GoalInput("采集企业信息", [], []),
        chat_context=[],
        resolved=ResolvedModel("deepseek", "deepseek-chat", "https://api.deepseek.com/v1", None),
        api_key="test-only-key",
    )

    assert fake.calls[0]["timeout_seconds"] == 6.5
    assert "thinking" not in fake.calls[0]["body"]
    assert "max_tokens" not in fake.calls[0]["body"]


@pytest.mark.asyncio
async def test_semantic_extraction_default_client_binds_custom_intent() -> None:
    """Changing semantic extraction's intent to Plan must leak private fields."""
    fake = FakeHttpClient(200, _response('{"fields":[]}'))
    agent = SemanticExtractionAgent(
        inference_settings=Settings(provider_inference_timeout_seconds=5.75),
        http=fake,
    )
    inp = SemanticExtractionInput(
        schema_version="m11.1",
        fields=[],
        unresolved_fields=[],
        readable_text="fixture",
        source_url="https://example.com",
        snapshot_id=1,
        run_id=1,
    )

    await agent.extract(
        inp,
        ResolvedModel("deepseek", "deepseek-chat", "https://api.deepseek.com/v1", None),
        api_key="test-only-key",
    )

    assert fake.calls[0]["timeout_seconds"] == 5.75
    assert "thinking" not in fake.calls[0]["body"]
    assert "max_tokens" not in fake.calls[0]["body"]
