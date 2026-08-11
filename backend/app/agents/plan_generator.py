"""PlanGeneratorAgent — Pydantic AI 受约束规划（M-08 / D-008）。

与 M-06 GoalUnderstandingAgent 同一模式：pydantic-ai 负责 typed 输出校验/重试循环，
真实 HTTP 调用走 M-03 ModelInferenceClient（用户自己的 ModelConfig + CredentialVault
解密 key），不引入第二套模型 SDK，也不把 Secret 送进 Prompt。

LLM 只输出 typed PlanGraphDraft；node_type 只能来自 NodeRegistry 允许清单。模型永远
不能输出 Python/Shell/任意 Tool name 作为执行动作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.domain.task_types import TaskType
from app.plan.schemas import PlanGraphDraft
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

PLAN_SYSTEM_PROMPT = (
    "你是 Kairos 网页信息采集 Agent 的计划生成模块。你的唯一职责：根据已确认的"
    " CollectionSpec 生成一个受约束的执行计划 JSON。\n"
    "规则：\n"
    "1. 只能使用下方允许的 node_type；绝不发明新节点。\n"
    "2. 计划必须完全落在已确认 Spec 的采集范围、字段与质量标准之内；不得扩大域名范围、"
    "不得改变字段含义、不得降低质量要求——这些属于规格层，不由计划决定。\n"
    "3. 节点通过 depends_on / edges 形成有向无环图；每个节点只依赖已存在的节点。\n"
    "4. parameters 只能包含下方节点契约中列出的字段（字段名、类型、required 以契约为准）；"
    "不得发明契约之外的键名，不得把数组字段写成对象。\n"
    "5. 只输出一个 JSON 对象，不要输出任何 JSON 之外的文字、markdown 或注释。\n"
    "6. reasoning_summary 只写可审计的执行思路摘要，不要暴露推理内部过程。\n"
    "7. 标准流水线（两阶段来源发现，D-068）：指定来源任务顺序为 "
    "access_rules_check → link_discovery → fetch → extract → normalize → validate → "
    "generate_artifact；探索/混合任务在流水线最前加 source_search。"
    "fetch 之前必须是 URL 资源（种子/站点扩展），fetch 产出 snapshot，"
    "extract 消费 snapshot 产出 record。\n"
    "8. 资源边语义：每条边 resource_refs 的 kind 必须被 from 节点的 output_contract 产出、"
    "且被 to 节点的 input_contract 消费；常见资源链为 url → snapshot → record → artifact。"
    "不要把 fetch 排在 access_rules_check 之前，也不要把 snapshot 当 url 传递。\n"
    "\n允许节点清单：{registry_json}\n"
    "执行约束：{constraints_json}\n"
    "Spec 内容：{spec_json}\n"
    "\n输出契约（示例资源链）：\n"
    '{{"schema_version": "m08.1", "task_id": 1, "spec_version": 1, '
    '"task_type": "SPECIFIED_SOURCE|EXPLORATORY|HYBRID", "nodes": ['
    '{{"node_id": "n1", "node_type": "access_rules_check", "definition_version": "1.0.0", '
    '"parameters": {{}}, "depends_on": [], "optional": false, "fail_policy": "block"}}, '
    '{{"node_id": "n2", "node_type": "link_discovery", "definition_version": "1.0.0", '
    '"parameters": {{}}, "depends_on": ["n1"], "optional": false, "fail_policy": "block"}}, '
    '{{"node_id": "n3", "node_type": "fetch", "definition_version": "1.0.0", '
    '"parameters": {{"url_template": "https://example.com"}}, "depends_on": ["n2"], '
    '"optional": false, "fail_policy": "block"}}, '
    '{{"node_id": "n4", "node_type": "extract", "definition_version": "1.0.0", '
    '"parameters": {{"fields": ["标题"]}}, "depends_on": ["n3"], '
    '"optional": false, "fail_policy": "block"}}], '
    '"edges": [{{"from_node_id": "n1", "to_node_id": "n2", '
    '"resource_refs": [{{"kind": "url", "ref_key": "url:1"}}]}}, '
    '{{"from_node_id": "n2", "to_node_id": "n3", '
    '"resource_refs": [{{"kind": "url", "ref_key": "url:2"}}]}}, '
    '{{"from_node_id": "n3", "to_node_id": "n4", '
    '"resource_refs": [{{"kind": "snapshot", "ref_key": "snap:1"}}]}}], '
    '"reasoning_summary": "..."}}'
)


def _system_prompt(inp: PlanInput) -> str:
    return PLAN_SYSTEM_PROMPT.format(
        registry_json=json.dumps(inp.registry_metadata, ensure_ascii=False),
        constraints_json=json.dumps(inp.execution_constraints, ensure_ascii=False),
        spec_json=json.dumps(inp.spec_payload, ensure_ascii=False),
    )


def _user_prompt(inp: PlanInput) -> str:
    spec_json = json.dumps(inp.spec_payload, ensure_ascii=False)
    return f"Spec JSON：{spec_json}\n任务类型：{inp.task_type.value}"


class PlanInput(BaseModel):
    spec_payload: dict
    task_type: TaskType
    registry_metadata: list[dict]
    execution_constraints: dict


def _to_user_text(messages: list[ModelMessage]) -> str:
    """Reassemble the user prompt from pydantic-ai messages for the wire call."""
    parts: list[str] = []
    for message in messages:
        if not hasattr(message, "parts"):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = part.content
                parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


@dataclass
class PlanGeneratorAgent:
    inference: ModelInferenceClient | None = None

    def __post_init__(self) -> None:
        self._inference = self.inference or ModelInferenceClient()

    def _build_function(self, resolved: ResolvedModel, api_key: str | None, inp: PlanInput):
        async def _call(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
            system = _system_prompt(inp)
            user = _to_user_text(messages) or _user_prompt(inp)
            result = await self._inference.generate(
                resolved=resolved, api_key=api_key, system=system, user=user
            )
            parsed = json.loads(result.text)
            tool_name = (
                agent_info.output_tools[0].name if agent_info.output_tools else "final_result"
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=tool_name, args=json.dumps(parsed, ensure_ascii=False))
                ]
            )

        return _call

    async def generate(
        self, inp: PlanInput, resolved: ResolvedModel, api_key: str | None
    ) -> PlanGraphDraft:
        agent = Agent(
            model=FunctionModel(self._build_function(resolved, api_key, inp)),
            output_type=PlanGraphDraft,
            system_prompt=_system_prompt(inp),
            retries=1,
        )
        result = await agent.run(_user_prompt(inp))
        return result.output
