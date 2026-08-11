"""PlanGenerationService — 生成 + 确定性校验 + 有界单次 repair（M-08）。

Provider 解析（require_available_model_config + CredentialVault 解密）在 API/路由层
完成并把已解密的 ``resolved`` + ``api_key`` 传入本服务；本服务不接触 Secret，也不做
第二套模型 SDK 调用。测试直接注入 Fake ``inference`` 并传任意 ``resolved``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanValidationIssue, PlanValidationResult
from app.plan.validator import validate_plan
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel


@dataclass
class PlanGenerationOutcome:
    graph: PlanGraphDraft
    validation_result: PlanValidationResult
    issues: list[PlanValidationIssue]
    repair_used: bool
    audit: dict[str, Any] = field(default_factory=dict)


class PlanGenerationService:
    def __init__(
        self,
        *,
        registry: NodeRegistry | None = None,
        inference: ModelInferenceClient | None = None,
    ) -> None:
        self._registry = registry or NodeRegistry()
        self._agent = PlanGeneratorAgent(inference=inference or ModelInferenceClient())

    async def _run_with_graph(
        self, spec_payload: dict, inp: PlanInput, resolved: ResolvedModel | None
    ) -> PlanGenerationOutcome:
        """单次生成 + 确定性校验（供 repair 循环与测试复用）。"""
        started = perf_counter()
        resolved_model = resolved or ResolvedModel("deepseek", "placeholder", None, None)
        graph = await self._agent.generate(inp, resolved_model, api_key=None)
        outcome = validate_plan(
            graph,
            spec_payload,
            self._registry,
            available_search=bool(inp.execution_constraints.get("has_search_provider")),
        )
        return PlanGenerationOutcome(
            graph=graph,
            validation_result=outcome.result,
            issues=outcome.issues,
            repair_used=False,
            audit={"duration_ms": int((perf_counter() - started) * 1000)},
        )

    async def _repair_loop(
        self,
        inp: PlanInput,
        resolved: ResolvedModel | None,
        api_key: str | None = None,
        *,
        max_repairs: int = 1,
    ) -> PlanGenerationOutcome:
        started = perf_counter()
        outcome = await self._run_with_graph(inp.spec_payload, inp, resolved)
        repair_used = False
        if outcome.validation_result == PlanValidationResult.INVALID and max_repairs > 0:
            repair_used = True
            # 把 Validator 的可纠正结构问题作为明确证据喂回模型（D-013 有证据纠错）
            repair_input = inp.model_copy(
                update={
                    "execution_constraints": {
                        **inp.execution_constraints,
                        "validator_issues": [i.model_dump(mode="json") for i in outcome.issues],
                    }
                }
            )
            outcome = await self._run_with_graph(inp.spec_payload, repair_input, resolved)
            outcome.repair_used = True
        outcome.audit["duration_ms"] = int((perf_counter() - started) * 1000)
        outcome.repair_used = repair_used
        return outcome
