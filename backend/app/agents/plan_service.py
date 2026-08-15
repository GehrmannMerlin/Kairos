"""PlanGenerationService — 生成 + 确定性校验 + 有界单次 repair（M-08）。

复用 M-06 GoalUnderstandingService 的 provider/vault 解析模式：API 层注入
``ProviderService`` + ``CredentialVault``，本服务在调用时解密真实用户模型的 API Key
并只在调用路径内存活；audit 元数据只保存 config_id/version、provider、model、duration，
不保存 key。测试注入 Fake ``inference``（不解析真实 provider）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.auth.models import User
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanValidationIssue, PlanValidationResult
from app.plan.validator import validate_plan
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from app.providers.registry import build_model_provider
from app.providers.service import ProviderService
from app.providers.transport import HttpClient

if TYPE_CHECKING:
    from app.config import Settings


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
        provider_service: ProviderService | None = None,
        vault: Any = None,
        registry: NodeRegistry | None = None,
        inference: ModelInferenceClient | None = None,
        settings: Settings | None = None,
        http: HttpClient | None = None,
        agent: PlanGeneratorAgent | None = None,
    ) -> None:
        from app.config import get_settings

        self._provider = provider_service
        self._vault = vault
        self._registry = registry or NodeRegistry()
        self._agent = agent or PlanGeneratorAgent(
            inference=inference,
            settings=settings or get_settings(),
            http=http,
        )

    def _resolve_model(self, user: User) -> tuple[ResolvedModel, str | None, Any]:
        if self._provider is None or self._vault is None:
            # 测试路径：不解析真实 provider，调用方需自行注入 Fake inference
            return ResolvedModel("deepseek", "placeholder", None, None), None, None
        config = self._provider.require_available_model_config(user)
        provider = build_model_provider(config.provider_type)
        resolved = provider.resolve_model(
            model=config.model_name,
            base_url=config.base_url,
            credential_version_id=config.credential_version_id,
        )
        api_key = None
        if config.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=user.id, credential_version_id=config.credential_version_id
            )
        return resolved, api_key, config

    def build_input(
        self, spec_payload: dict, task_type: TaskType, user: Any | None = None
    ) -> PlanInput:
        # list_search_configs 需要真实 user.id；传 None 会 AttributeError（Gate-2 真实
        # provider 发现）。测试路径 provider 为 None 时短路为 has_search=False。
        has_search = bool(
            self._provider is not None
            and user is not None
            and self._provider.list_search_configs(user)
        )
        return PlanInput(
            spec_payload=spec_payload,
            task_type=task_type,
            registry_metadata=self._registry.planning_metadata(),
            execution_constraints={"has_search_provider": has_search},
        )

    async def _run_with_graph(
        self,
        spec_payload: dict,
        inp: PlanInput,
        resolved: ResolvedModel | None,
        api_key: str | None = None,
    ) -> PlanGenerationOutcome:
        """单次生成 + 确定性校验（供 repair 循环与测试复用）。

        api_key 必须从调用方传入：generate_for_task 已从 CredentialVault 解密出真实
        ModelConfig 的 key，若在此处丢弃，推理请求会无 Authorization header（真实
        Provider → 401），这是 Gate-2 真实 Provider 关闭时发现的回归。
        """
        started = perf_counter()
        resolved_model = resolved or ResolvedModel("deepseek", "placeholder", None, None)
        graph = await self._agent.generate(inp, resolved_model, api_key=api_key)
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
        outcome = await self._run_with_graph(inp.spec_payload, inp, resolved, api_key=api_key)
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
            outcome = await self._run_with_graph(
                inp.spec_payload, repair_input, resolved, api_key=api_key
            )
            outcome.repair_used = True
        outcome.audit["duration_ms"] = int((perf_counter() - started) * 1000)
        outcome.repair_used = repair_used
        return outcome

    async def generate_for_task(
        self, *, user: User, spec_payload: dict, task_type: TaskType
    ) -> PlanGenerationOutcome:
        resolved, api_key, config = self._resolve_model(user)
        inp = self.build_input(spec_payload, task_type, user=user)
        outcome = await self._repair_loop(inp, resolved, api_key, max_repairs=1)
        if config is not None:
            outcome.audit.update(
                {
                    "model_config_id": config.config_id,
                    "model_config_version": config.version,
                    "provider": config.provider_type,
                    "model": config.model_name,
                }
            )
        return outcome
