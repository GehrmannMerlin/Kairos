"""SemanticExtractionAgent — Pydantic AI typed fallback (D-010 / 二十六~三十二).

同一模式：pydantic-ai Agent + FunctionModel 包装 M-03 ModelInferenceClient；
LLM 只输出 typed SemanticExtractionResult，绝不返回 Markdown 再 regex 解析。
只发送 unresolved fields + 有界上下文；Secrets 绝不进入 prompt（十二）。
retries=1：第一次格式/Schema 失败允许一次 repair，第二次仍失败即结束（三十三）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.extraction.contracts import ExtractionSettings
from app.providers.inference import ModelInferenceClient
from app.providers.inference_factory import build_inference_client
from app.providers.inference_policy import InferenceIntent
from app.providers.protocol import ResolvedModel
from app.providers.transport import HttpClient

if TYPE_CHECKING:
    from app.config import Settings

_STRICT = ConfigDict(extra="forbid")

EXTRACTION_SYSTEM_PROMPT = (
    "你是 Kairos 网页信息采集 Agent 的字段提取模块。你的唯一职责：只对给定的 unresolved "
    "字段做语义提取，返回一个 JSON 对象。\n"
    "规则：\n"
    "1. 只能从下方页面上下文中提取；禁止编造页面不存在的值。\n"
    "2. evidence_quote 必须逐字来自页面正文，用于程序化验证；没有可靠 quote 就不填。\n"
    "3. 已由确定性规则得到的字段不要重复输出。\n"
    "4. 每个字段的 value 必须匹配其 type（url/email/phone/number/date/text）。\n"
    "5. confidence 是你自己的不确定度（0~1），系统会重新计算最终置信度。\n"
    "6. 可选：proposed_selector 提供一个你认为可靠的 CSS 选择器（用于规则学习候选），"
    "不确定就留空；你只提出候选，是否生效由程序验证决定。\n"
    "7. 无法提取的字段在 missing_reason 说明原因。\n"
    '只输出一个 JSON 对象：{"fields": [{"field_name": string, "value": string, '
    '"evidence_quote": string, "source_locator": string|null, "confidence": number, '
    '"missing_reason": string|null, "proposed_selector": string|null}]}。'
    "不要输出 JSON 之外的任何文字。"
)


class SemanticFieldCandidate(BaseModel):
    model_config = _STRICT

    field_name: str
    value: str = ""
    evidence_quote: str = ""
    source_locator: str | None = None
    confidence: float = 0.0
    missing_reason: str | None = None
    proposed_selector: str | None = None


class SemanticExtractionResult(BaseModel):
    model_config = _STRICT

    fields: list[SemanticFieldCandidate] = Field(default_factory=list)


class SemanticExtractionInput(BaseModel):
    """LLM 输入最小化：只含冻结 Spec 的 unresolved 字段 + 有界上下文 + 确定性摘要。"""

    model_config = _STRICT

    schema_version: str
    fields: list[dict]
    unresolved_fields: list[str]
    known_candidates: list[dict] = Field(default_factory=list)
    readable_text: str = ""
    source_url: str = ""
    snapshot_id: int
    run_id: int


def _system_prompt(inp: SemanticExtractionInput) -> str:
    return EXTRACTION_SYSTEM_PROMPT + (
        "\n\nSpec 字段："
        + json.dumps(inp.fields, ensure_ascii=False)
        + "\n需要提取的字段："
        + json.dumps(inp.unresolved_fields, ensure_ascii=False)
        + "\n已知确定性结果（不要重复提取）："
        + json.dumps(inp.known_candidates, ensure_ascii=False)
    )


def _user_prompt(inp: SemanticExtractionInput) -> str:
    return (
        f"页面正文：{inp.readable_text}\n来源 URL：{inp.source_url}\n"
        f"snapshot_id：{inp.snapshot_id}\nrun_id：{inp.run_id}"
    )


def _to_user_text(messages: list[ModelMessage]) -> str:
    """Reassemble user prompt (+ retry validation errors) for the wire call."""
    parts: list[str] = []
    for message in messages:
        if not hasattr(message, "parts"):
            continue
        for part in message.parts:
            if isinstance(part, (UserPromptPart, RetryPromptPart)):
                content = part.content
                parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


@dataclass
class SemanticExtractionAgent:
    inference: ModelInferenceClient | None = None
    settings: ExtractionSettings = field(default_factory=ExtractionSettings)
    inference_settings: Settings | None = None
    http: HttpClient | None = None

    def __post_init__(self) -> None:
        from app.config import get_settings

        self._inference = self.inference or build_inference_client(
            intent=InferenceIntent.CUSTOM_AGENT,
            settings=self.inference_settings or get_settings(),
            http=self.http,
        )
        self._resolved: ResolvedModel | None = None
        self._api_key: str | None = None

    def bind_model(self, resolved: ResolvedModel, api_key: str | None) -> None:
        """执行期绑定真实模型（由 executor 通过 ExtractionModelResolver 设置）。"""
        self._resolved = resolved
        self._api_key = api_key

    def _build_function(
        self, resolved: ResolvedModel, api_key: str | None, inp: SemanticExtractionInput
    ):
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

    async def extract(
        self,
        inp: SemanticExtractionInput,
        resolved: ResolvedModel | None = None,
        api_key: str | None = None,
    ) -> SemanticExtractionResult:
        model = resolved if resolved is not None else self._resolved
        key = api_key if api_key is not None else self._api_key
        if model is None:
            model = ResolvedModel(
                provider_type="placeholder",
                model_name="none",
                base_url=None,
                credential_version_id=None,
            )
        agent = Agent(
            model=FunctionModel(self._build_function(model, key, inp)),
            output_type=SemanticExtractionResult,
            system_prompt=_system_prompt(inp),
            retries=self.settings.llm_max_repairs,  # 一次 repair，绝无无限调用（三十三）
        )
        result = await agent.run(_user_prompt(inp))
        return result.output
