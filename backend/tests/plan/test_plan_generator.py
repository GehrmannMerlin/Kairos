"""M-08 Task 2: PlanGenerator via Fake Model Adapter (no real provider).

Covers the high-value cases only: typed structured plan, unknown node rejected by
Validator, one bounded repair pass, second failure → FAIL.
"""

from __future__ import annotations

import pytest
from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.agents.plan_service import PlanGenerationService, PlanValidationFailure
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
        task_id=1,
        spec_version=1,
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
async def test_plan_identity_is_canonicalized_from_command_context() -> None:
    agent = PlanGeneratorAgent(inference=FakeInference(VALID_PLAN_JSON))
    inp = _input().model_copy(
        update={"task_id": 25, "spec_version": 3, "task_type": TaskType.SPECIFIED_SOURCE}
    )
    graph = await agent.generate(inp, RESOLVED, api_key=None)
    assert graph.task_id == 25
    assert graph.spec_version == 3
    assert graph.task_type is TaskType.SPECIFIED_SOURCE


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
async def test_generator_prompt_teaches_pipeline_order() -> None:
    """Regression (Gate-2 real provider): 真实 LLM 会误解标准流水线（把 fetch 排在
    access_rules_check 前、跳过 link_discovery、snapshot 当 url 传递），导致
    RESOURCE_EDGE_INCOMPATIBLE。prompt 必须给出规范流水线与资源链语义。"""
    fake = FakeInference(VALID_PLAN_JSON)
    agent = PlanGeneratorAgent(inference=fake)
    await agent.generate(_input(), RESOLVED, api_key=None)
    assert "access_rules_check" in fake.system_seen
    assert "link_discovery" in fake.system_seen
    assert "fetch" in fake.system_seen
    assert "output_contract" in fake.system_seen


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
    assert outcome.audit["generation_calls"] == 2
    assert "generation_duration_ms" in outcome.audit
    assert "validation_duration_ms" in outcome.audit


@pytest.mark.asyncio
async def test_api_key_is_forwarded_to_inference() -> None:
    """Regression (Gate-2 real provider): 真实 ModelConfig 解密出的 api_key 必须
    贯穿 repair 循环到达推理调用；否则 DeepSeek 无 Authorization header → 401 → 500。"""

    class RecordingInference(ModelInferenceClient):
        def __init__(self) -> None:
            self.api_key_seen: str | None = None

        async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
            self.api_key_seen = api_key
            return InferenceResult(text=VALID_PLAN_JSON, provider_type="deepseek", duration_ms=1)

    fake = RecordingInference()
    service = PlanGenerationService(inference=fake)
    outcome = await service._repair_loop(_input(), RESOLVED, api_key="sk-gate2-test", max_repairs=1)
    assert fake.api_key_seen == "sk-gate2-test"
    assert outcome.validation_result in (
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
    )


@pytest.mark.asyncio
async def test_build_input_passes_user_to_search_config_check() -> None:
    """Regression (Gate-2 real provider): build_input 必须把真实 user 传给
    list_search_configs；否则传 None → user.id AttributeError → plan 生成 500。"""

    class _FakeUser:
        id = 7

    class _FakeProvider:
        def __init__(self) -> None:
            self.seen_user: object | None = None

        def list_search_configs(self, user):
            self.seen_user = user
            return []

    provider = _FakeProvider()
    service = PlanGenerationService(provider_service=provider)
    inp = service.build_input(
        _input().spec_payload,
        TaskType.SPECIFIED_SOURCE,
        user=_FakeUser(),
        task_id=25,
        spec_version=3,
    )
    assert provider.seen_user is not None
    assert provider.seen_user.id == 7
    assert inp.execution_constraints["has_search_provider"] is False
    assert inp.task_id == 25
    assert inp.spec_version == 3


@pytest.mark.asyncio
async def test_second_failure_is_blocked() -> None:
    always_bad = (
        '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
        '"task_type":"SPECIFIED_SOURCE",'
        '"nodes":[{"node_id":"n1","node_type":"ssh_into_server","definition_version":"1.0.0",'
        '"parameters":{},"depends_on":[],"optional":false,"fail_policy":"block"}],"edges":[]}'
    )
    fake = FakeInference(always_bad)
    service = PlanGenerationService(inference=fake)
    with pytest.raises(PlanValidationFailure) as caught:
        await service._repair_loop(_input(), RESOLVED, api_key=None, max_repairs=1)
    assert caught.value.issues[0].code == "NODE_NOT_REGISTERED"
