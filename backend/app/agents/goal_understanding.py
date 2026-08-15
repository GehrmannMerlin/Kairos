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
from typing import TYPE_CHECKING

from app.agents.schemas import GoalUnderstandingResult
from app.providers.inference import ModelInferenceClient
from app.providers.inference_factory import build_inference_client
from app.providers.inference_policy import InferenceIntent
from app.providers.protocol import ResolvedModel
from app.providers.transport import HttpClient
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

if TYPE_CHECKING:
    from app.config import Settings

GOAL_UNDERSTANDING_SYSTEM_PROMPT = (
    "你是 Kairos 网页信息采集 Agent 的目标理解模块。你的唯一职责：把用户的采集需求"
    "转换成结构化的任务规格 JSON。严格区分三种任务类型：\n"
    "- EXPLORATORY：用户只给主题和期望字段，没有具体网址，需要自行搜索并发现来源。\n"
    "- SPECIFIED_SOURCE：用户直接给出网站/URL/栏目，从这些指定来源批量提取。\n"
    "- HYBRID：先搜索发现目标网站，再从这些官网采集字段。\n"
    "\n"
    "你必须只输出一个 JSON 对象，且必须严格遵循下面的字段契约，不得增删顶层字段，"
    "不得使用其他键名：\n"
    '{"task_type": "EXPLORATORY|SPECIFIED_SOURCE|HYBRID",\n'
    ' "goal": "一句话采集目标（必填，字符串）",\n'
    ' "fields": [{"name": "字段中文名", "type": "text|number|url|email|phone|date|boolean|other", '
    '"required": true, "description": "可选说明"}],\n'
    ' "auto_expand_fields": false,\n'
    ' "source_scope": {"mode": "EXPLORATORY|SPECIFIED_SOURCE|HYBRID", '
    '"seed_urls": [], "source_hints": []},\n'
    ' "completion_conditions": [{"kind": "min_records|range_covered|saturation|limit", '
    '"target": 20, "threshold": null, "note": null}],\n'
    ' "advanced_runtime_limits": {"max_pages": null, "max_duration_minutes": null, '
    '"max_retries_per_url": null},\n'
    ' "confidence": 0.9,\n'
    ' "ambiguities": [],\n'
    ' "clarification_required": false,\n'
    ' "clarification_question": null,\n'
    ' "template_variables": [{"name": "city", "label": "城市", "value": "深圳"}]}\n'
    "\n"
    "字段契约说明：\n"
    "1. completion_conditions 必须是数组，每个元素必须含 kind（只能取上面四个值之一），"
    "不能是单个对象。\n"
    "2. fields 数组使用简洁中文名；type 取值必须是 text/number/url/email/phone/date/boolean/other；"
    "核心字段 required=true。\n"
    "3. source_scope.seed_urls 只放用户明确提供的网址；"
    "source_hints 放用户描述或你推断的来源提示。\n"
    "4. confidence 是 0～1 的浮点数，表示你对任务类型和字段判定的把握。\n"
    "5. 若信息不足以确定采集范围（既无网址也无明确实体列表），"
    "clarification_required=true 并只问一个最影响任务范围的高杠杆问题，"
    "写在 clarification_question，不要一次问五六个。\n"
    "6. 若检测到'深圳'这类单次条件值适合做成模板变量，"
    "在 template_variables 给出 name/label/value。\n"
    "7. 不要输出任何 JSON 之外的文字、markdown 代码块或注释。"
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
    def __init__(
        self,
        inference: ModelInferenceClient | None = None,
        *,
        settings: Settings | None = None,
        http: HttpClient | None = None,
    ) -> None:
        from app.config import get_settings

        self._inference = inference or build_inference_client(
            intent=InferenceIntent.GOAL_EXTRACTION,
            settings=settings or get_settings(),
            http=http,
        )

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
