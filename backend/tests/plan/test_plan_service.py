"""Bounded model invocation behavior for plan generation."""

from __future__ import annotations

import pytest
from app.agents.plan_generator import PlanGeneratorAgent
from app.agents.plan_service import PlanGenerationService, PlanValidationFailure
from app.domain.task_types import TaskType
from app.providers import errors
from app.providers.inference import InferenceResult, ModelInferenceClient
from tests.plan.test_plan_generator import RESOLVED, VALID_PLAN_JSON, _input

INVALID_PLAN_JSON = (
    '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
    '"task_type":"SPECIFIED_SOURCE",'
    '"nodes":[{"node_id":"n1","node_type":"ssh_into_server",'
    '"definition_version":"1.0.0","parameters":{},"depends_on":[]}],"edges":[]}'
)


class RecordingSequenceInference(ModelInferenceClient):
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0
        self.system_prompts: list[str] = []

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        self.system_prompts.append(system)
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return InferenceResult(text=text, provider_type="deepseek", duration_ms=1)


def test_build_input_keeps_trusted_task_and_spec_identity() -> None:
    service = PlanGenerationService(inference=RecordingSequenceInference([INVALID_PLAN_JSON]))
    inp = service.build_input(
        _input().spec_payload,
        TaskType.SPECIFIED_SOURCE,
        task_id=25,
        spec_version=3,
    )
    assert inp.task_id == 25
    assert inp.spec_version == 3


@pytest.mark.asyncio
async def test_service_validates_the_trusted_task_type_not_the_model_task_type() -> None:
    service = PlanGenerationService(inference=RecordingSequenceInference([VALID_PLAN_JSON]))
    outcome = await service._run_with_graph(_input().spec_payload, _input(), RESOLVED)
    assert outcome.graph.task_type is TaskType.SPECIFIED_SOURCE


@pytest.mark.asyncio
async def test_permanently_invalid_plan_stops_after_two_model_calls() -> None:
    inference = RecordingSequenceInference([INVALID_PLAN_JSON, INVALID_PLAN_JSON])
    service = PlanGenerationService(inference=inference)

    with pytest.raises(PlanValidationFailure) as caught:
        await service._repair_loop(_input(), RESOLVED, api_key=None, max_repairs=99)

    assert inference.calls == 2
    assert caught.value.issues
    assert caught.value.issues[0].code == "NODE_NOT_REGISTERED"
    assert caught.value.audit["generation_calls"] == 2
    assert "original_graph" in inference.system_prompts[1]
    assert "complete replacement graph" in inference.system_prompts[1]


@pytest.mark.asyncio
async def test_malformed_json_is_typed_and_not_retried_by_agent() -> None:
    inference = RecordingSequenceInference(["not-json"])
    agent = PlanGeneratorAgent(inference=inference)

    with pytest.raises(errors.ProviderInferenceError):
        await agent.generate(_input(), RESOLVED, api_key=None)

    assert inference.calls == 1


@pytest.mark.asyncio
async def test_schema_invalid_json_is_not_retried_by_agent() -> None:
    inference = RecordingSequenceInference(['{"nodes":"not-a-list"}'])
    agent = PlanGeneratorAgent(inference=inference)

    with pytest.raises(errors.ProviderInferenceError):
        await agent.generate(_input(), RESOLVED, api_key=None)

    assert inference.calls == 1
