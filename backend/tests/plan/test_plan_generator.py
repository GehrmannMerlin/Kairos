"""M-08 Task 2: PlanGenerator via Fake Model Adapter (no real provider).

Covers the high-value cases only: typed structured plan, unknown node rejected by
Validator, one bounded repair pass, second failure → FAIL.
"""

from __future__ import annotations

import pytest
from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.agents.plan_service import PlanGenerationService
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanValidationResult
from app.providers.inference import InferenceResult, ModelInferenceClient
from app.providers.protocol import ResolvedModel

RESOLVED = ResolvedModel("deepseek", "deepseek-chat", None, None)

VALID_PLAN_JSON = (
    '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
    '"task_type":"SPECIFIED_SOURCE",'
    '"nodes":['
    '{"node_id":"n1","node_type":"fetch","definition_version":"1.0.0",'
    '"parameters":{"url_template":"https://example.com/item/{id}"},"depends_on":[],"optional":false,"fail_policy":"block"},'
    '{"node_id":"n2","node_type":"extract","definition_version":"1.0.0",'
    '"parameters":{"fields":["公司名"]},"depends_on":["n1"],"optional":false,"fail_policy":"block"},'
    '{"node_id":"n3","node_type":"generate_artifact","definition_version":"1.0.0",'
    '"parameters":{"format":"csv"},"depends_on":["n2"],"optional":false,"fail_policy":"block"}'
    "],"
    '"edges":['
    '{"from_node_id":"n1","to_node_id":"n2","resource_refs":[{"kind":"snapshot","ref_key":"snap:1"}]},'
    '{"from_node_id":"n2","to_node_id":"n3","resource_refs":[{"kind":"record","ref_key":"rec:1"}]}'
    "],"
    '"reasoning_summary":"对指定来源逐页抓取并抽取字段"}'
)


class FakeInference(ModelInferenceClient):
    def __init__(self, text: str) -> None:
        self._text = text
        self.user_seen: str | None = None
        self.system_seen: str | None = None

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        self.system_seen = system
        self.user_seen = user
        return InferenceResult(text=self._text, provider_type="deepseek", duration_ms=1)


def _input() -> PlanInput:
    spec = SpecDraftPayload(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="抓取指定网站的公司信息",
        fields=[{"name": "公司名", "type": "text", "required": True}],
        source_scope={
            "mode": "SPECIFIED_SOURCE",
            "seed_urls": ["https://example.com"],
            "source_hints": [],
        },
    )
    return PlanInput(
        spec_payload=spec.model_dump(mode="json"),
        task_type=TaskType.SPECIFIED_SOURCE,
        registry_metadata=NodeRegistry().planning_metadata(),
        execution_constraints={"has_search_provider": False},
    )


@pytest.mark.asyncio
async def test_generator_returns_typed_plan() -> None:
    agent = PlanGeneratorAgent(inference=FakeInference(VALID_PLAN_JSON))
    graph = await agent.generate(_input(), RESOLVED, api_key=None)
    assert isinstance(graph, PlanGraphDraft)
    assert graph.task_type == TaskType.SPECIFIED_SOURCE
    assert [n.node_type for n in graph.nodes] == ["fetch", "extract", "generate_artifact"]
    assert graph.nodes[1].depends_on == ["n1"]


@pytest.mark.asyncio
async def test_generator_passes_registry_metadata_to_model() -> None:
    fake = FakeInference(VALID_PLAN_JSON)
    agent = PlanGeneratorAgent(inference=fake)
    await agent.generate(_input(), RESOLVED, api_key=None)
    assert fake.system_seen is not None
    assert "fetch" in fake.system_seen
    # 注册表元数据只暴露规划所需信息，不暴露 Python class path / SDK 实现
    assert "parameter_schema" not in fake.system_seen
    assert "ModuleType" not in fake.system_seen


@pytest.mark.asyncio
async def test_unknown_node_is_rejected_by_validator() -> None:
    bad = (
        '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
        '"task_type":"SPECIFIED_SOURCE",'
        '"nodes":[{"node_id":"n1","node_type":"ssh_into_server","definition_version":"1.0.0",'
        '"parameters":{},"depends_on":[],"optional":false,"fail_policy":"block"}],"edges":[]}'
    )
    service = PlanGenerationService(inference=FakeInference(bad))
    spec = _input().spec_payload
    outcome = await service._run_with_graph(spec, _input(), None)
    assert outcome.validation_result == PlanValidationResult.INVALID


@pytest.mark.asyncio
async def test_one_repair_then_pass() -> None:
    # 第一次：模型输出了未注册 node_type（可纠正结构问题 → NODE_NOT_REGISTERED INVALID）
    bad = (
        '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
        '"task_type":"SPECIFIED_SOURCE",'
        '"nodes":[{"node_id":"n1","node_type":"ssh_into_server","definition_version":"1.0.0",'
        '"parameters":{},"depends_on":[],"optional":false,"fail_policy":"block"}],"edges":[]}'
    )

    class SeqInference(ModelInferenceClient):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
            self.calls += 1
            return InferenceResult(
                text=VALID_PLAN_JSON if self.calls >= 2 else bad,
                provider_type="deepseek",
                duration_ms=1,
            )

    fake = SeqInference()
    service = PlanGenerationService(inference=fake)
    outcome = await service._repair_loop(_input(), RESOLVED, api_key=None, max_repairs=1)
    assert outcome.repair_used is True
    assert outcome.validation_result in (
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
    )


@pytest.mark.asyncio
async def test_second_failure_is_blocked() -> None:
    always_bad = (
        '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
        '"task_type":"SPECIFIED_SOURCE",'
        '"nodes":[{"node_id":"n1","node_type":"ssh_into_server","definition_version":"1.0.0",'
        '"parameters":{},"depends_on":[],"optional":false,"fail_policy":"block"}],"edges":[]}'
    )
    service = PlanGenerationService(inference=FakeInference(always_bad))
    outcome = await service._repair_loop(_input(), RESOLVED, api_key=None, max_repairs=1)
    assert outcome.validation_result == PlanValidationResult.INVALID
    assert outcome.repair_used is True
