"""GoalUnderstandingAgent typed output via pydantic-ai + fake inference (TEST B).

No real provider / network: the fake inference client feeds canned JSON, and the
pydantic-ai Agent still validates it into a typed GoalUnderstandingResult.
"""

from __future__ import annotations

import pytest
from app.agents.goal_understanding import GoalInput, GoalUnderstandingAgent
from app.providers.inference import InferenceResult, ModelInferenceClient
from app.providers.protocol import ResolvedModel

RESOLVED = ResolvedModel("openai", "gpt-4o-mini", "https://api.openai.com/v1", None)


class FakeInference(ModelInferenceClient):
    def __init__(self, text: str) -> None:
        self._text = text
        self.system_seen: str | None = None
        self.user_seen: str | None = None

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        self.system_seen = system
        self.user_seen = user
        return InferenceResult(text=self._text, provider_type="openai", duration_ms=1)


def _exploratory_json(goal: str = "搜集深圳工业自动化设备供应商") -> str:
    return (
        '{"task_type":"EXPLORATORY","goal":"' + goal + '",'
        '"fields":[{"name":"公司名","type":"text","required":true},'
        '{"name":"官网","type":"url","required":false}],'
        '"auto_expand_fields":true,'
        '"source_scope":{"mode":"EXPLORATORY","seed_urls":[],'
        '"source_hints":["深圳 工业自动化 供应商"]},'
        '"completion_conditions":[{"kind":"min_records","target":20}],'
        '"confidence":0.9,"clarification_required":false}'
    )


def _specified_json() -> str:
    return (
        '{"task_type":"SPECIFIED_SOURCE",'
        '"goal":"从指定网站提取供应商",'
        '"fields":[{"name":"供应商","type":"text","required":true}],'
        '"source_scope":{"mode":"SPECIFIED_SOURCE",'
        '"seed_urls":["https://example.com/suppliers"],"source_hints":[]},'
        '"completion_conditions":[{"kind":"range_covered"}],'
        '"confidence":0.85,"clarification_required":false}'
    )


def _hybrid_json() -> str:
    return (
        '{"task_type":"HYBRID","goal":"先找官网再采集产品",'
        '"fields":[{"name":"官网","type":"url","required":true}],'
        '"source_scope":{"mode":"HYBRID","seed_urls":[],'
        '"source_hints":["深圳 工业机器人 厂商官网"]},'
        '"completion_conditions":[],'
        '"confidence":0.8,"clarification_required":false}'
    )


def _clarification_json() -> str:
    return (
        '{"task_type":"EXPLORATORY","goal":"采集企业信息",'
        '"fields":[],"source_scope":{"mode":"EXPLORATORY","seed_urls":[],"source_hints":[]},'
        '"completion_conditions":[],'
        '"confidence":0.4,"clarification_required":true,'
        '"clarification_question":"请提供要采集的企业名单或网址？"}'
    )


async def _understand(agent: GoalUnderstandingAgent, goal_input: GoalInput):
    return await agent.understand(
        goal_input=goal_input, chat_context=[], resolved=RESOLVED, api_key="sk-test"
    )


@pytest.mark.asyncio
async def test_exploratory_typed_output() -> None:
    agent = GoalUnderstandingAgent(inference=FakeInference(_exploratory_json()))
    result = await _understand(
        agent, GoalInput(goal_text="搜集深圳工业自动化设备供应商", seed_urls=[], source_hints=[])
    )
    assert result.task_type.value == "EXPLORATORY"
    assert result.fields[0].name == "公司名"
    assert result.fields[0].required is True
    assert result.auto_expand_fields is True
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_specified_source_typed_output() -> None:
    agent = GoalUnderstandingAgent(inference=FakeInference(_specified_json()))
    result = await _understand(
        agent,
        GoalInput(
            goal_text="从网站提取", seed_urls=["https://example.com/suppliers"], source_hints=[]
        ),
    )
    assert result.task_type.value == "SPECIFIED_SOURCE"
    assert result.source_scope.seed_urls == ["https://example.com/suppliers"]


@pytest.mark.asyncio
async def test_hybrid_typed_output() -> None:
    agent = GoalUnderstandingAgent(inference=FakeInference(_hybrid_json()))
    result = await _understand(
        agent, GoalInput(goal_text="先找官网再采集", seed_urls=[], source_hints=[])
    )
    assert result.task_type.value == "HYBRID"


@pytest.mark.asyncio
async def test_clarification_typed_output() -> None:
    agent = GoalUnderstandingAgent(inference=FakeInference(_clarification_json()))
    result = await _understand(
        agent, GoalInput(goal_text="采集这几个企业的信息", seed_urls=[], source_hints=[])
    )
    assert result.clarification_required is True
    assert "企业名单" in (result.clarification_question or "")
    assert len(result.ambiguities) == 0


@pytest.mark.asyncio
async def test_draft_context_is_sent_to_model() -> None:
    fake = FakeInference(_exploratory_json())
    agent = GoalUnderstandingAgent(inference=fake)
    await _understand(
        agent,
        GoalInput(
            goal_text="搜集供应商",
            seed_urls=["https://a.com", "https://b.com"],
            source_hints=["官方"],
        ),
    )
    assert "https://a.com" in fake.user_seen
    assert "官方" in fake.user_seen
