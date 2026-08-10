"""GoalUnderstandingAgent — Pydantic AI goal understanding (M-06).

Responsibility is narrow: turn the Task Draft Context + necessary Chat Context
into a typed ``GoalUnderstandingResult`` (D-003 exploratory/specified/hybrid,
field candidates, source scope, completion conditions, clarification). It is NOT
a plan/crawler/extractor agent.

Integration rule (M-03 reuse, no second SDK): pydantic-ai drives the typed
output validation/retry loop via ``FunctionModel``, but the actual HTTP call is
performed by ``ModelInferenceClient`` against the user's own resolved
ModelConfig + CredentialVault-decrypted key. No pydantic-ai built-in provider
adapter is used, so no second model SDK and no key leak path.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.schemas import GoalUnderstandingResult
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

GOAL_UNDERSTANDING_SYSTEM_PROMPT = (
    "你是 Kairos 网页信息采集 Agent 的目标理解模块。你的唯一职责：把用户的采集需求"
    "转换成结构化的任务规格 JSON。严格区分三种任务类型：\n"
    "- EXPLORATORY：用户只给主题和期望字段，没有具体网址，需要自行搜索并发现来源。\n"
    "- SPECIFIED_SOURCE：用户直接给出网站/URL/栏目，从这些指定来源批量提取。\n"
    "- HYBRID：先搜索发现目标网站，再从这些官网采集字段。\n"
    "要求：\n"
    "1. 只输出一个合法 JSON 对象，不要输出任何额外文字或 markdown。\n"
    "2. fields 使用简洁中文名；type 取值 text/number/url/email/phone/date/boolean/other；"
    "核心字段 required=true。\n"
    "3. source_scope.seed_urls 只放用户明确提供的网址；"
    "source_hints 放用户描述或你推断的来源提示。\n"
    "4. completion_conditions 表达 D-006 多条件完成语义"
    "（min_records/range_covered/saturation/limit）。\n"
    "5. 若信息不足以确定采集范围（既无网址也无明确实体列表），"
    "clarification_required=true 并只问一个最影响任务范围的高杠杆问题，不要一次问五六个。\n"
    "6. 若检测到'深圳'这类单次条件值适合做成模板变量，在 template_variables 给出 name/label/value。"
)


@dataclass
class GoalInput:
    goal_text: str
    seed_urls: list[str]
    source_hints: list[str]


def _prompt_content_to_str(content: str | Sequence[object]) -> str:
    if isinstance(content, str):
        return content
    # Sequence of UserContent (text/image/...); keep only plain text.
    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
        elif isinstance(item, TextContent):
            text_parts.append(item.content)
    return " ".join(text_parts)


def _extract_messages(messages: list[ModelMessage]) -> tuple[str, str]:
    """Reassemble system + user text from pydantic-ai messages for the wire call."""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                system_parts.append(part.content)
            elif isinstance(part, UserPromptPart):
                user_parts.append(_prompt_content_to_str(part.content))
            elif isinstance(part, RetryPromptPart):
                retry_text = _prompt_content_to_str(part.content)
                user_parts.append(f"（上一轮输出校验失败，请修正：{retry_text}）")
    return "\n".join(system_parts), "\n".join(user_parts)


def _build_user_prompt(goal_input: GoalInput, chat_context: list[str]) -> str:
    lines = [f"用户需求：{goal_input.goal_text}"]
    if goal_input.seed_urls:
        lines.append("用户指定网址：" + "、".join(goal_input.seed_urls))
    if goal_input.source_hints:
        lines.append("来源提示：" + "、".join(goal_input.source_hints))
    if chat_context:
        lines.append("补充对话：" + "；".join(chat_context[-6:]))
    return "\n".join(lines)


class GoalUnderstandingAgent:
    def __init__(self, inference: ModelInferenceClient | None = None) -> None:
        self._inference = inference or ModelInferenceClient()

    def _build_function(self, resolved: ResolvedModel, api_key: str | None):
        async def _call(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
            system, user = _extract_messages(messages)
            inference_result = await self._inference.generate(
                resolved=resolved, api_key=api_key, system=system, user=user
            )
            parsed = json.loads(inference_result.text)
            tool_name = (
                agent_info.output_tools[0].name if agent_info.output_tools else "final_result"
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=tool_name,
                        args=json.dumps(parsed, ensure_ascii=False),
                    )
                ]
            )

        return _call

    async def understand(
        self,
        *,
        goal_input: GoalInput,
        chat_context: list[str],
        resolved: ResolvedModel,
        api_key: str | None,
    ) -> GoalUnderstandingResult:
        prompt = _build_user_prompt(goal_input, chat_context)
        agent = Agent(
            model=FunctionModel(self._build_function(resolved, api_key)),
            output_type=GoalUnderstandingResult,
            system_prompt=GOAL_UNDERSTANDING_SYSTEM_PROMPT,
            retries=1,
        )
        result = await agent.run(prompt)
        return result.output
